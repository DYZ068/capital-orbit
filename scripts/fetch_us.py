#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股產業資金流 → data/us.json

資金流定義：Chaikin 錢流乘數套在 15 分 K 線上
    MFM  = ((C − L) − (H − C)) / (H − L)      範圍 −1 ~ +1
    flow = MFM × C × V                        C×V 是這根 K 線的成交額

資料源（--provider / US_PROVIDER）：
  alpaca（預設）  批次請求，需要免費 API key（alpaca.markets 的 Paper Trading key）。
                  免費層是 IEX 報價源，成交量只涵蓋全市場一部分，但各產業抽樣比例
                  一致，看「相對」資金流沒問題；絕對金額偏小，別跟其他來源對數字。
  yahoo           不用 key 的退路。逐檔抓，雲端 IP 多半會被 429 擋掉，僅供本機臨時測試。

三個代價很高的教訓，都寫成程式碼裡的防線了：
  1. 沒有整輪時限 → 一次失敗跑了 2 小時。現在有 DEADLINE_SECONDS 與熔斷器。
  2. 股號格式各家不同 → BRK.B 直接送給 Alpaca 會讓整批 HTTP 400。現在按資料源轉換。
  3. Alpaca 多檔查詢的 limit 是「整批總筆數」不是「每檔筆數」→ 後面幾批被截斷。
     現在改用時間窗 start/end + next_page_token 分頁，不靠 limit 截斷。
"""

import argparse
import io
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gics_zh import zh_sector, zh_sub  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "us.json")
# v2：舊快取存的是 Yahoo 格式的股號（BRK-B），會害 Alpaca 整批 400，所以換檔名強制重建
CACHE = os.path.join(HERE, "..", "data", "_sp500_v2.json")

WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
ALPACA_BARS = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_CLOCK = "https://api.alpaca.markets/v2/clock"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

BAR_SECONDS = 15 * 60
WINDOW_MINUTES = 90         # 往回抓的時間窗，夠涵蓋數根 K 線又不會塞爆一頁
MAX_BAR_AGE = 60 * 60       # 最新 K 線超過這麼舊就不發布，避免把收盤資料標成「即時」
DEADLINE_SECONDS = 240
BREAKER_PROBE = 20
BREAKER_RATIO = 0.9
ALPACA_CHUNK = 180
ALPACA_PAGE_LIMIT = 10000   # 單頁上限；真正的完整性靠 next_page_token 分頁保證
CHECK_SAMPLE = 40           # check 模式抽驗幾檔，要夠多才算得出有意義的覆蓋率
YAHOO_WORKERS = 4
YAHOO_ATTEMPTS = 2
MIN_COVERAGE = 0.60
MIN_INDUSTRY_FLOW = 0.02
UNIT = 1e8
CACHE_MAX_AGE = 7 * 86400


# ------------------------------------------------------------- 股號格式轉換

def to_provider_symbol(sym, provider):
    """維基用 BRK.B；Alpaca 也用 BRK.B；Yahoo 用 BRK-B。以維基寫法為準再轉。"""
    return sym.replace(".", "-") if provider == "yahoo" else sym


def from_provider_symbol(sym, provider):
    return sym.replace("-", ".") if provider == "yahoo" else sym


# ------------------------------------------------------------- 預算與熔斷

class Budget:
    def __init__(self, seconds):
        self.t0 = time.time()
        self.limit = seconds
        self.tried = self.failed = 0
        self.reason = None
        self.fatal = False          # 金鑰錯之類的問題，一定要紅燈
        self._lock = threading.Lock()

    def record(self, ok):
        with self._lock:
            self.tried += 1
            if not ok:
                self.failed += 1
            if (self.tried >= BREAKER_PROBE and self.reason is None
                    and self.failed / self.tried >= BREAKER_RATIO):
                self.reason = (f"熔斷：前 {self.tried} 次請求失敗 {self.failed} 次"
                               f"（{self.failed/self.tried:.0%}），資料源不通，立刻放棄")

    def fail(self, reason, fatal=False):
        self.reason, self.fatal = reason, fatal

    @property
    def spent(self):
        return time.time() - self.t0

    def stop(self):
        if self.reason:
            return True
        if self.spent > self.limit:
            self.reason = f"超過總時限 {self.limit}s，中止本輪"
            return True
        return False


# ------------------------------------------------------------- 成分股

def sp500(force=False):
    """回傳 [(維基股號, GICS 部門, GICS 子產業)]，股號保留維基原樣（BRK.B）。"""
    if not force and os.path.exists(CACHE):
        if time.time() - os.path.getmtime(CACHE) < CACHE_MAX_AGE:
            with open(CACHE, encoding="utf-8") as f:
                return [tuple(x) for x in json.load(f)]
    import pandas as pd
    r = requests.get(WIKI, timeout=45, headers={"User-Agent": BROWSER_UA})
    r.raise_for_status()
    df = next(t for t in pd.read_html(io.StringIO(r.text))
              if "Symbol" in t.columns and "GICS Sector" in t.columns)
    sub_col = "GICS Sub-Industry" if "GICS Sub-Industry" in df.columns else "GICS Sub Industry"
    rows = [(str(x["Symbol"]).strip(), str(x["GICS Sector"]).strip(), str(x[sub_col]).strip())
            for _, x in df.iterrows()]
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return rows


def closed_enough(bar_start_epoch, now=None):
    return bar_start_epoch + BAR_SECONDS <= (now if now is not None else time.time())


def pick_closed_bar(bars, now=None):
    """從一串 K 線裡挑出最新的、已經收完的那根。bars 依時間由舊到新。"""
    for b in reversed(bars):
        ts, o, h, l, c, v = b
        if not closed_enough(ts, now):
            continue
        if None in (o, h, l, c, v) or v <= 0 or h <= l:
            continue
        return (float(o), float(h), float(l), float(c), float(v), ts)
    return None


# ------------------------------------------------------------- alpaca

def alpaca_session(key, secret):
    s = requests.Session()
    s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                      "accept": "application/json"})
    return s


def alpaca_market_open(sess):
    """回傳 True / False / None（問不到）。用來分辨『休市』與『真的壞掉』。"""
    try:
        r = sess.get(ALPACA_CLOCK, timeout=20)
        if r.status_code != 200:
            return None
        return bool(r.json().get("is_open"))
    except (requests.exceptions.RequestException, ValueError):
        return None


def alpaca_bars(symbols, budget, sess):
    """symbols 是維基格式。回傳 {維基股號: (o,h,l,c,v,epoch)}。"""
    out = {}
    start = (datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)) \
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")
    chunks = [symbols[i:i + ALPACA_CHUNK] for i in range(0, len(symbols), ALPACA_CHUNK)]

    for n, chunk in enumerate(chunks, 1):
        if budget.stop():
            break
        # 用時間窗界定範圍，不用 limit 截斷。
        # limit 在多檔查詢時算的是「整批總筆數」，拿它當每檔數量會讓後面的股票被吃掉。
        params = {"symbols": ",".join(to_provider_symbol(s, "alpaca") for s in chunk),
                  "timeframe": "15Min", "start": start,
                  "limit": ALPACA_PAGE_LIMIT, "adjustment": "raw",
                  "feed": os.environ.get("ALPACA_FEED", "iex")}
        merged = defaultdict(list)
        pages = 0
        while True:
            if budget.stop():
                break
            try:
                r = sess.get(ALPACA_BARS, params=params, timeout=45)
            except requests.exceptions.RequestException as e:
                budget.record(False)
                print(f"  批次 {n}/{len(chunks)} 連線失敗：{type(e).__name__}")
                break
            if r.status_code in (401, 403):
                budget.fail(f"Alpaca 金鑰無效或權限不足（HTTP {r.status_code}）", fatal=True)
                return out
            if r.status_code != 200:
                budget.record(False)
                print(f"  批次 {n}/{len(chunks)} HTTP {r.status_code}: {r.text[:160]}")
                break
            try:
                body = r.json()
            except ValueError:
                budget.record(False)
                break
            bars = body.get("bars")
            if bars is None:
                budget.fail(f"Alpaca 回應沒有 bars 欄位：{str(body)[:160]}", fatal=True)
                return out
            for sym, arr in (bars or {}).items():
                merged[sym].extend(arr or [])
            pages += 1
            token = body.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        params.pop("page_token", None)

        got = 0
        for sym, arr in merged.items():
            rows = []
            for b in arr:
                try:
                    ts = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError, AttributeError, TypeError):
                    continue
                rows.append((ts, b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")))
            rows.sort(key=lambda x: x[0])
            picked = pick_closed_bar(rows)
            if picked:
                out[from_provider_symbol(sym, "alpaca")] = picked
                got += 1
        budget.record(got > 0)
        print(f"  批次 {n}/{len(chunks)}：{got}/{len(chunk)} 檔，{pages} 頁"
              f"（累計 {len(out)}，已用 {budget.spent:.0f}s）")
    return out


# ------------------------------------------------------------- yahoo

def yahoo_bars(symbols, budget):
    out, lock = {}, threading.Lock()
    sess = requests.Session()
    sess.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})

    def one(canonical):
        if budget.stop():
            return
        sym = to_provider_symbol(canonical, "yahoo")
        for _ in range(YAHOO_ATTEMPTS):
            try:
                r = sess.get(YAHOO_CHART.format(sym),
                             params={"range": "1d", "interval": "15m"}, timeout=15)
            except requests.exceptions.RequestException:
                time.sleep(0.5)
                continue
            if r.status_code != 200:
                time.sleep(0.5)
                continue
            try:
                res = r.json()["chart"]["result"][0]
                ts, q = res["timestamp"], res["indicators"]["quote"][0]
            except (KeyError, IndexError, TypeError, ValueError):
                break
            rows = [(ts[i], q["open"][i], q["high"][i], q["low"][i],
                     q["close"][i], q["volume"][i]) for i in range(len(ts))]
            picked = pick_closed_bar(rows)
            if picked:
                with lock:
                    out[canonical] = picked
                budget.record(True)
                return
            break
        budget.record(False)

    with ThreadPoolExecutor(max_workers=YAHOO_WORKERS) as ex:
        list(ex.map(one, symbols))
    return out


# ------------------------------------------------------------- 判定

def verdict(coverage, market_open, fatal, threshold=MIN_COVERAGE):
    """回傳 (exit_code, 訊息)。抽出來是為了能單元測試，不用真的連線。

    market_open: True 開盤 / False 休市 / None 問不到
    """
    if fatal:
        return 1, "資料源設定有問題（金鑰或回應結構），必須修好"
    if coverage >= threshold:
        return 0, f"覆蓋率 {coverage:.0%}，通過（門檻 {threshold:.0%}）"
    if market_open is False:
        return 0, f"覆蓋率 {coverage:.0%}，但目前休市，本來就不會有新 K 線——不算失敗"
    if market_open is None:
        return 1, (f"覆蓋率 {coverage:.0%} 未達門檻 {threshold:.0%}，"
                   f"而且問不到開收盤狀態，當成失敗處理")
    return 1, f"覆蓋率 {coverage:.0%} 未達門檻 {threshold:.0%}，盤中卻抓不到資料，是真的壞了"


# ------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["alpaca", "yahoo"],
                    default=os.environ.get("US_PROVIDER", "alpaca"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--check", action="store_true",
                    help="抽驗資料源；覆蓋率不足會回非零結束碼")
    ap.add_argument("--refresh-list", action="store_true")
    a = ap.parse_args()

    members = sp500(force=a.refresh_list)
    if a.check:
        members = members[:CHECK_SAMPLE]
    elif a.limit:
        members = members[:a.limit]
    symbols = [m[0] for m in members]

    budget = Budget(90 if a.check else DEADLINE_SECONDS)
    print(f"資料源 {a.provider}｜{len(symbols)} 檔｜時限 {budget.limit}s")

    market_open = None
    if a.provider == "alpaca":
        key, secret = os.environ.get("ALPACA_KEY", ""), os.environ.get("ALPACA_SECRET", "")
        if not key or not secret:
            print("！沒有設定 ALPACA_KEY / ALPACA_SECRET。")
            print("  到 alpaca.markets 免費註冊，拿 Paper Trading 的 API key，")
            print("  設成 GitHub Secrets，或改用 --provider yahoo。")
            return 1 if a.check else 0
        sess = alpaca_session(key, secret)
        market_open = alpaca_market_open(sess)
        print(f"美股狀態：{'盤中' if market_open else '休市' if market_open is False else '問不到'}")
        bars = alpaca_bars(symbols, budget, sess)
    else:
        bars = yahoo_bars(symbols, budget)

    cover = len(bars) / len(symbols) if symbols else 0.0
    print(f"取得 {len(bars)}/{len(symbols)} 檔（{cover:.0%}），耗時 {budget.spent:.0f}s")
    if budget.reason:
        print(f"！{budget.reason}")

    code, msg = verdict(cover, market_open, budget.fatal)

    if a.check:
        print(f"\n{'✓' if code == 0 else '✗'} {msg}")
        if code == 0 and bars:
            s, b = next(iter(bars.items()))
            print(f"  範例 {s}: O={b[0]} H={b[1]} L={b[2]} C={b[3]} V={b[4]}")
        return code

    if code != 0:
        print(f"✗ {msg}，不更新檔案。")
        return code
    if not bars or cover < MIN_COVERAGE:
        print(f"{msg}，不更新檔案。")
        return 0

    newest = max(b[5] for b in bars.values())
    age = time.time() - newest
    if age > MAX_BAR_AGE:
        print(f"最新 K 線已是 {age/60:.0f} 分鐘前，太舊了不當成即時資料發布，不更新檔案。")
        return 0

    agg = defaultdict(lambda: {"net": 0.0, "w": 0.0, "chgw": 0.0, "sector": ""})
    for sym, sector, sub in members:
        b = bars.get(sym)
        if not b:
            continue
        o, h, l, c, v, _ = b
        x = agg[zh_sub(sub)]
        x["sector"] = zh_sector(sector)
        x["net"] += ((c - l) - (h - c)) / (h - l) * c * v
        x["w"] += c * v
        x["chgw"] += ((c - o) / o * 100.0) * c * v if o else 0.0

    rows = [{"name": k, "sector": x["sector"], "net": round(x["net"] / UNIT, 2),
             "chg": round(x["chgw"] / x["w"], 2) if x["w"] else 0.0}
            for k, x in agg.items() if abs(x["net"] / UNIT) >= MIN_INDUSTRY_FLOW]
    rows.sort(key=lambda r: -r["net"])
    if len(rows) < 2:
        print("有效產業不足兩個，不更新檔案。")
        return 0

    inflow = sum(r["net"] for r in rows if r["net"] > 0)
    outflow = -sum(r["net"] for r in rows if r["net"] < 0)
    pair, base = min(inflow, outflow), max(inflow, outflow)

    stamp = datetime.fromtimestamp(newest, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        stamp = stamp.astimezone(ZoneInfo("Asia/Taipei"))
    except Exception:
        pass

    payload = {"meta": {
        "market": "美股 S&P 500", "unit": "億美元",
        "snapshot": stamp.strftime("%Y-%m-%d %H:%M"), "freq": "每 15 分鐘更新",
        "source": f"{a.provider} 15 分 K 線 · Chaikin 錢流乘數 · GICS 子產業（覆蓋 {cover:.0%}）",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, "industries": rows}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"產業數 {len(rows)}｜流入 {inflow:.2f}｜流出 -{outflow:.2f}"
          f"｜可配對 {pair:.2f}｜未配對 {(base-pair)/base*100:.0f}%（單位：億美元）")
    print(f"已寫入 {os.path.normpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
