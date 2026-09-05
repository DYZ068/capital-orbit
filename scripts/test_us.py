#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_us.py 的離線測試，不連網路。重點在驗證那些「出事時」的行為，
因為這支腳本真正的風險不是算錯，是失敗時拖垮排程。

    python scripts/test_us.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_us as F  # noqa: E402

FAILED = []


def check(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol if isinstance(want, float) and isinstance(got, float) else got == want
    print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"   應為 {want!r}"))
    if not ok:
        FAILED.append(name)


print("1. K 線收完了沒（尾端兩根不能用的關鍵判斷）")
now = time.time()
check("30 分鐘前開始的 → 收完了", F.closed_enough(now - 1800), True)
check("7 分鐘前開始的 → 還在跑", F.closed_enough(now - 420), False)
check("剛剛好 15 分鐘 → 收完了", F.closed_enough(now - 900), True)
check("14 分 59 秒 → 還在跑", F.closed_enough(now - 899), False)

print("\n2. 熔斷器（上一版沒有這個，才會跑掉兩小時）")
b = F.Budget(240)
for _ in range(19):
    b.record(False)
check("19 次失敗還沒熔斷", b.reason is None, True)
check("還沒喊停", b.stop(), False)
b.record(False)
check("第 20 次觸發熔斷", b.reason is not None, True)
check("熔斷後立刻喊停", b.stop(), True)

b2 = F.Budget(240)
for _ in range(20):
    b2.record(True)
check("全部成功不會誤觸熔斷", b2.reason is None, True)

b3 = F.Budget(240)
for i in range(20):
    b3.record(i % 4 != 0)          # 75% 成功
check("75% 成功率不熔斷（門檻 90% 失敗）", b3.reason is None, True)

print("\n3. 總時限")
b4 = F.Budget(0.05)
time.sleep(0.1)
check("超時就喊停", b4.stop(), True)
check("而且說得出原因", "時限" in (b4.reason or ""), True)
check("預設時限遠小於排程間隔 900s", F.DEADLINE_SECONDS < 900, True)

print("\n4. Chaikin 錢流乘數")
mfm = lambda o, h, l, c: ((c - l) - (h - c)) / (h - l)
check("收在最高點 = +1", mfm(100, 110, 90, 110), 1.0)
check("收在最低點 = −1", mfm(100, 110, 90, 90), -1.0)
check("收在正中間 = 0", mfm(100, 110, 90, 100), 0.0)
check("收在高點附近 = 0.8", mfm(100, 110, 90, 108), 0.8)
check("資金流 = MFM × 收盤 × 量", mfm(100, 110, 90, 108) * 108 * 1000, 86400.0)

print("\n5. 產業彙總與中文對照")
rows = [("A", "Semiconductors", 100.0, 110.0, 90.0, 108.0, 1000.0),
        ("B", "Semiconductors", 50.0, 52.0, 48.0, 49.0, 2000.0)]
net = w = chgw = 0.0
for _, _, o, h, l, c, v in rows:
    d = c * v
    net += mfm(o, h, l, c) * d
    w += d
    chgw += (c - o) / o * 100 * d
check("兩檔加總", net, 86400 + (-0.5 * 98000))
check("成交額加權漲跌幅", chgw / w, (8.0 * 108000 - 2.0 * 98000) / (108000 + 98000))
check("子產業中文", F.zh_sub("Semiconductors"), "半導體")
check("部門中文", F.zh_sector("Energy"), "能源")
check("查不到就保留原文", F.zh_sub("Totally Made Up"), "Totally Made Up")

print("\n6. 覆蓋率保護")
check("門檻 60%", F.MIN_COVERAGE, 0.60)
check("320/503 = 64% 通過", (320 / 503) >= F.MIN_COVERAGE, True)
check("300/503 = 60% 差一點，擋下", (300 / 503) >= F.MIN_COVERAGE, False)

print("\n7. 股號格式（BRK.B 送給 Alpaca 會讓整批 400）")
check("維基 BRK.B → Alpaca 保持不變", F.to_provider_symbol("BRK.B", "alpaca"), "BRK.B")
check("維基 BRK.B → Yahoo 轉成 BRK-B", F.to_provider_symbol("BRK.B", "yahoo"), "BRK-B")
check("維基 BF.B → Alpaca 保持不變", F.to_provider_symbol("BF.B", "alpaca"), "BF.B")
check("維基 BF.B → Yahoo 轉成 BF-B", F.to_provider_symbol("BF.B", "yahoo"), "BF-B")
check("一般股號不受影響", F.to_provider_symbol("AAPL", "alpaca"), "AAPL")
check("Yahoo 回來的 BRK-B 轉回 BRK.B", F.from_provider_symbol("BRK-B", "yahoo"), "BRK.B")
check("Alpaca 回來的不用轉", F.from_provider_symbol("BRK.B", "alpaca"), "BRK.B")
# 快取換檔名，避免舊的 BRK-B 格式繼續毒害 Alpaca
check("快取檔名已換版", "_sp500_v2.json" in F.CACHE, True)

print("\n8. 挑 K 線：不靠 limit 截斷，也不能挑到還在跑的那根")
now = 1_700_000_000
series = [(now-3600, 10, 11, 9, 10.5, 5000),
          (now-1800, 20, 22, 18, 21.0, 8000),   # 已收完
          (now-420,  30, 31, 29, 30.0, 400000), # 還在跑
          (now-5,    31, 31, 31, 31.0, 0)]      # 合成報價點
got = F.pick_closed_bar(series, now=now)
check("挑到已收完那根", got[3], 21.0)
check("不是還在跑那根", got[4] != 400000.0, True)
check("全部都還沒收完就回 None",
      F.pick_closed_bar([(now-60, 1, 2, 0.5, 1.5, 100)], now=now), None)
check("成交量 0 的不算", F.pick_closed_bar([(now-1800, 1, 2, 0.5, 1.5, 0)], now=now), None)
check("H==L 會除以零，要跳過", F.pick_closed_bar([(now-1800, 5, 5, 5, 5, 100)], now=now), None)
check("時間窗夠涵蓋數根 15 分 K", F.WINDOW_MINUTES >= 45, True)

print("\n9. check 的判定（假成功就是從這裡漏掉的）")
v = F.verdict
check("盤中覆蓋 5% → 紅燈", v(0.05, True, False)[0], 1)
check("盤中覆蓋 64% → 綠燈", v(0.64, True, False)[0], 0)
check("盤中剛好 60% → 綠燈", v(0.60, True, False)[0], 0)
check("盤中 59% → 紅燈", v(0.59, True, False)[0], 1)
check("休市且沒資料 → 不算失敗", v(0.0, False, False)[0], 0)
check("休市但有足夠資料 → 一樣綠燈", v(0.8, False, False)[0], 0)
check("問不到開收盤又覆蓋不足 → 保守判失敗", v(0.05, None, False)[0], 1)
check("金鑰錯一律紅燈", v(0.9, True, True)[0], 1)
check("紅燈時說得出原因", len(v(0.05, True, False)[1]) > 10, True)

print()
if FAILED:
    print(f"✗ {len(FAILED)} 項失敗：{FAILED}")
    sys.exit(1)
print("✓ 全部通過")
