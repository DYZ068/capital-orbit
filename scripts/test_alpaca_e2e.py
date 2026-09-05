#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用一台模擬 Alpaca 的本機伺服器做端到端測試，不需要真金鑰。

刻意重現真實 API 的兩個行為：
  · 單頁最多回 N 筆 bar（整批總數，不是每檔），超過就給 next_page_token
  · 股號用 BRK.B 這種點號格式；收到 BRK-B 會回 400

要驗證的是：503 檔跑完之後覆蓋率是 100%，沒有任何一檔因為分頁或股號被吃掉。

    python scripts/test_alpaca_e2e.py
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_us as F  # noqa: E402

PAGE_ROWS = 400          # 故意設小，逼出分頁
BARS_PER_SYMBOL = 5
STATE = {"pages_served": 0, "bad_symbol": None, "max_rows_in_page": 0}


def make_bars(symbol, now):
    out = []
    for k in range(BARS_PER_SYMBOL, 0, -1):
        ts = now - k * 900
        base = 100 + (hash(symbol) % 50)
        out.append({"t": datetime.fromtimestamp(ts, timezone.utc)
                    .isoformat().replace("+00:00", "Z"),
                    "o": base, "h": base + 2, "l": base - 2,
                    "c": base + 1.2, "v": 10000 + (hash(symbol) % 5000)})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.endswith("/clock"):
            return self._send(200, {"is_open": True})

        syms = q.get("symbols", [""])[0].split(",")
        # 真實 Alpaca 收到 BRK-B 會 400。這裡忠實重現，才能抓到股號格式的錯。
        for s in syms:
            if "-" in s:
                STATE["bad_symbol"] = s
                return self._send(400, {"message": f"invalid symbol: {s}"})

        now = int(time.time())
        rows = [(s, b) for s in syms for b in make_bars(s, now)]
        start = int(q.get("page_token", ["0"])[0])
        page = rows[start:start + PAGE_ROWS]
        STATE["pages_served"] += 1
        STATE["max_rows_in_page"] = max(STATE["max_rows_in_page"], len(page))

        bars = {}
        for s, b in page:
            bars.setdefault(s, []).append(b)
        body = {"bars": bars}
        nxt = start + PAGE_ROWS
        if nxt < len(rows):
            body["next_page_token"] = str(nxt)
        self._send(200, body)


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    F.ALPACA_BARS = f"http://127.0.0.1:{port}/v2/stocks/bars"
    F.ALPACA_CLOCK = f"http://127.0.0.1:{port}/v2/clock"

    members = F.sp500()
    symbols = [m[0] for m in members]
    dotted = [s for s in symbols if "." in s]
    print(f"成分股 {len(symbols)} 檔，其中帶點號的：{dotted}")

    sess = F.alpaca_session("fake", "fake")
    print(f"開收盤狀態：{F.alpaca_market_open(sess)}")

    budget = F.Budget(120)
    t0 = time.time()
    bars = F.alpaca_bars(symbols, budget, sess)
    cover = len(bars) / len(symbols)

    print(f"\n分頁：伺服器送出 {STATE['pages_served']} 頁，單頁最多 {STATE['max_rows_in_page']} 筆")
    print(f"覆蓋率 {len(bars)}/{len(symbols)} = {cover:.1%}，耗時 {time.time()-t0:.1f}s")

    fails = []
    if STATE["bad_symbol"]:
        fails.append(f"送出了 Alpaca 不接受的股號 {STATE['bad_symbol']}")
    if cover < 1.0:
        missing = [s for s in symbols if s not in bars][:8]
        fails.append(f"覆蓋率不是 100%，漏了 {missing}")
    for d in dotted:
        if d not in bars:
            fails.append(f"帶點號的 {d} 沒有拿回來")
    if STATE["pages_served"] <= len(symbols) // F.ALPACA_CHUNK:
        fails.append("完全沒有觸發分頁，這個測試沒測到東西")
    code, msg = F.verdict(cover, True, budget.fatal)
    if code != 0:
        fails.append(f"判定居然是失敗：{msg}")

    print()
    if fails:
        for f in fails:
            print(f"  ✗ {f}")
        srv.shutdown()
        return 1
    print(f"  ✓ 503 檔全部拿回，帶點號的 {dotted} 也在內")
    print(f"  ✓ 分頁有被觸發並正確合併（{STATE['pages_served']} 頁）")
    print(f"  ✓ 判定：{msg}")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
