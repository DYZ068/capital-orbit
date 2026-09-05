#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把某個產業的資金流拆回個股，產生一張可以人工複查的稽核表。

用途：拿到任何數字都要能追回原始出處。這支腳本印出每一檔的
「法人買賣超股數 × 當日均價 = 金額」，妳可以隨機挑幾檔，
到證交所網站查同一天的 T86 與收盤行情，逐項對。

    python scripts/verify_tw.py 電腦及週邊
    python scripts/verify_tw.py 半導體業 --top 15
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_tw as T  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("industry")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    code = next((k for k, (n, _) in T.INDUSTRY.items() if n == a.industry), None)
    if not code:
        sys.exit(f"找不到產業「{a.industry}」。可選：{'、'.join(n for n,_ in T.INDUSTRY.values())}")

    day_rows = T.fetch_json(T.DAY_ALL)
    iso, ymd = T.roc_to_iso(day_rows[0]["Date"])
    price = {}
    for r in day_rows:
        vol, val = T.num(r.get("TradeVolume")), T.num(r.get("TradeValue"))
        close, chg = T.num(r.get("ClosingPrice")), T.num(r.get("Change"))
        avg = val / vol if vol > 0 else close
        if avg > 0:
            price[r["Code"]] = (avg, close, chg, r.get("Name", "").strip())

    t86 = T.fetch_json(T.T86, date=ymd, selectType="ALL", response="json")
    f = t86["fields"]
    ic, i_f, i_fd, i_t = (f.index("證券代號"), f.index("外陸資買賣超股數(不含外資自營商)"),
                          f.index("外資自營商買賣超股數"), f.index("投信買賣超股數"))
    parts = {}
    for row in t86["data"]:
        c = str(row[ic]).strip()
        parts[c] = (T.num(row[i_f]), T.num(row[i_fd]), T.num(row[i_t]))

    company = json.load(open(T.CAT_CACHE, encoding="utf-8")) \
        if os.path.exists(T.CAT_CACHE) else \
        {r["公司代號"]: r["產業別"].strip() for r in T.fetch_json(T.COMPANY)}

    rows = []
    for c, cat in company.items():
        if cat != code or c not in parts or c not in price:
            continue
        fore, fdlr, trust = parts[c]
        shares = fore + fdlr + trust
        avg, close, chg, name = price[c]
        rows.append((c, name, fore, fdlr, trust, shares, avg, shares * avg, close, chg))
    rows.sort(key=lambda r: -abs(r[7]))
    total = sum(r[7] for r in rows)

    print(f"\n【{a.industry}】{iso}   代碼 {code}   共 {len(rows)} 檔")
    print(f"公式：(外陸資 + 外資自營商 + 投信) 買賣超股數 × 當日均價\n")
    print(f"{'代號':<7}{'名稱':<9}{'外陸資':>13}{'外資自營':>10}{'投信':>12}"
          f"{'合計股數':>14}{'均價':>9}{'金額(億)':>11}")
    print("─" * 88)
    for c, name, fo, fd, tr, sh, avg, amt, *_ in rows[:a.top]:
        print(f"{c:<7}{name:<9}{fo:>13,.0f}{fd:>10,.0f}{tr:>12,.0f}"
              f"{sh:>14,.0f}{avg:>9.2f}{amt/1e8:>11.2f}")
    if len(rows) > a.top:
        rest = sum(r[7] for r in rows[a.top:])
        print(f"{'':<7}{f'其餘 {len(rows)-a.top} 檔':<9}{'':>49}{rest/1e8:>11.2f}")
    print("─" * 88)
    print(f"{'':<7}{'合計':<9}{'':>49}{total/1e8:>11.2f}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tw.json")
    if os.path.exists(out):
        d = json.load(open(out, encoding="utf-8"))
        pub = next((r["net"] for r in d["industries"] if r["name"] == a.industry), None)
        if pub is not None:
            diff = abs(pub - total / 1e8)
            print(f"\n圖上發布的數字：{pub:.2f} 億")
            print(f"這裡重算的結果：{total/1e8:.2f} 億")
            print(f"差異：{diff:.4f} 億  → {'✓ 對得上（四捨五入誤差）' if diff < 0.02 else '✗ 對不上，要查'}")

    print(f"\n人工複查方式：到 twse.com.tw 查 {iso} 的")
    print(f"  · 三大法人買賣超日報（T86）→ 對上面三欄股數")
    print(f"  · 每日收盤行情 → 均價 = 成交金額 ÷ 成交股數，不是收盤價")
    return 0


if __name__ == "__main__":
    sys.exit(main())
