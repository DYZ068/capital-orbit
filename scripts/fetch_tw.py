#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股產業資金流（每交易日收盤後）→ data/tw.json

資料源全部是證交所官方、免費、不用註冊也不用 token：
  1. STOCK_DAY_ALL   每日收盤行情（全部上市證券）→ 均價、成交值、漲跌
  2. T86             三大法人買賣超日報（個股）    → 外資＋投信買賣超股數
  3. t187ap03_L      上市公司基本資料              → 產業別代碼

資金流定義：
    淨買賣超股數 = 外陸資 + 外資自營商 + 投信
    資金流(元)   = 淨買賣超股數 × 當日均價（成交值 ÷ 成交股數）

T86 給的是「股數」不是「金額」，所以一定要自己乘均價才會是資金流。
台股盤中看不到法人資料（收盤後才公布），這個口徑本質上只能是日頻。

用法：
    pip install requests
    python scripts/fetch_tw.py
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tw.json")
# 公司產業別一週才變動一次，快取起來可以少打一支證交所 API，
# 減少被 WAF 擋掉的機會（三支只要有一支被擋，整輪就白跑）
CAT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "_twse_industry.json")
CAT_MAX_AGE = 7 * 86400
HIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "hist")

# 注意：證交所的 WAF 會擋掉自訂 User-Agent，回一頁「因為安全性考量」的 HTML，
# 狀態碼卻還是 200。所以這裡一律用 requests 的預設標頭，不要加 UA。

DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
COMPANY = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"

MIN_INDUSTRY_FLOW = 0.01     # 億元，低於此值視為雜訊
UNIT = 1e8                   # 元 -> 億元

# 證交所產業別代碼 -> (產業名稱, 上層族群)
INDUSTRY = {
    "01": ("水泥工業", "原物料"),   "02": ("食品工業", "民生"),
    "03": ("塑膠工業", "原物料"),   "04": ("紡織纖維", "民生"),
    "05": ("電機機械", "工業"),     "06": ("電器電纜", "工業"),
    "08": ("玻璃陶瓷", "原物料"),   "09": ("造紙工業", "原物料"),
    "10": ("鋼鐵工業", "原物料"),   "11": ("橡膠工業", "原物料"),
    "12": ("汽車工業", "工業"),     "14": ("建材營造", "營建"),
    "15": ("航運業", "運輸"),       "16": ("觀光餐旅", "服務"),
    "17": ("金融保險", "金融"),     "18": ("貿易百貨", "民生"),
    "19": ("綜合企業", "其他"),     "20": ("其他業", "其他"),
    "21": ("化學工業", "原物料"),   "22": ("生技醫療", "醫療"),
    "23": ("油電燃氣", "公用"),     "24": ("半導體業", "電子"),
    "25": ("電腦及週邊", "電子"),   "26": ("光電業", "電子"),
    "27": ("通信網路業", "電子"),   "28": ("電子零組件", "電子"),
    "29": ("電子通路業", "電子"),   "30": ("資訊服務業", "電子"),
    "31": ("其他電子業", "電子"),   "35": ("綠能環保", "工業"),
    "36": ("數位雲端", "電子"),     "37": ("運動休閒", "民生"),
    "38": ("居家生活", "民生"),
    # 91 是存託憑證(DR)，不是產業，直接略過
}


def fetch_json(url, **params):
    last = ""
    for attempt in range(5):
        try:
            r = requests.get(url, params=params or None, timeout=60)
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code in (429, 503):
            time.sleep(6 * (attempt + 1))
            continue
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return r.json()
        # WAF 擋掉時會回 200 + HTML，退避後重試
        last = " ".join(r.text.split())[:120]
        time.sleep(5 * (attempt + 1))
    sys.exit(f"連續失敗：{url}\n最後一次回應：{last}")


def num(s, default=0.0):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


def roc_to_iso(roc):
    """1150903 -> ('2026-09-03', '20260903')"""
    roc = str(roc).strip()
    y = int(roc[:-4]) + 1911
    return f"{y}-{roc[-4:-2]}-{roc[-2:]}", f"{y}{roc[-4:-2]}{roc[-2:]}"


def main():
    # 1. 收盤行情。這支 API 永遠回「最新交易日」，所以用它決定要抓哪一天，
    #    法人資料才不會跟行情對不上。
    day_rows = fetch_json(DAY_ALL)
    if not day_rows:
        print("收盤行情是空的，不更新檔案。")
        return 0
    iso, ymd = roc_to_iso(day_rows[0]["Date"])
    print(f"最新交易日 {iso}，收盤行情 {len(day_rows)} 筆")

    price = {}
    for r in day_rows:
        vol, val = num(r.get("TradeVolume")), num(r.get("TradeValue"))
        close, chg = num(r.get("ClosingPrice")), num(r.get("Change"))
        avg = val / vol if vol > 0 else close
        if avg <= 0:
            continue
        prev = close - chg
        price[r["Code"]] = (avg, (chg / prev * 100.0) if prev > 0 else 0.0)

    # 2. 三大法人買賣超（股數）
    t86 = fetch_json(T86, date=ymd, selectType="ALL", response="json")
    if t86.get("stat") != "OK" or not t86.get("data"):
        print(f"T86 沒有 {iso} 的資料（{t86.get('stat')}），不更新檔案。")
        return 0
    f = t86["fields"]
    i_code = f.index("證券代號")
    i_fore = f.index("外陸資買賣超股數(不含外資自營商)")
    i_fdlr = f.index("外資自營商買賣超股數")
    i_trust = f.index("投信買賣超股數")
    shares = {}
    for row in t86["data"]:
        code = str(row[i_code]).strip()
        shares[code] = num(row[i_fore]) + num(row[i_fdlr]) + num(row[i_trust])
    print(f"法人買賣超 {len(shares)} 檔（含權證等非個股）")

    # 3. 產業別（快取一週）
    company = None
    if os.path.exists(CAT_CACHE) and time.time() - os.path.getmtime(CAT_CACHE) < CAT_MAX_AGE:
        try:
            with open(CAT_CACHE, encoding="utf-8") as fp:
                company = json.load(fp)
            print(f"上市公司 {len(company)} 家（用快取）")
        except (ValueError, OSError):
            company = None
    if company is None:
        company = {r["公司代號"]: r["產業別"].strip() for r in fetch_json(COMPANY)}
        os.makedirs(os.path.dirname(CAT_CACHE), exist_ok=True)
        with open(CAT_CACHE, "w", encoding="utf-8") as fp:
            json.dump(company, fp, ensure_ascii=False)
        print(f"上市公司 {len(company)} 家（重新抓取並快取）")

    # 4. 彙總
    agg = defaultdict(lambda: {"net": 0.0, "w": 0.0, "chgw": 0.0, "sector": "", "n": 0})
    matched = 0
    for code, sh in shares.items():
        cat = company.get(code)
        if not cat or cat not in INDUSTRY or code not in price:
            continue
        avg, chg = price[code]
        flow = sh * avg
        name, sector = INDUSTRY[cat]
        a = agg[name]
        a["sector"] = sector
        a["net"] += flow
        w = abs(flow)
        a["w"] += w
        a["chgw"] += chg * w
        a["n"] += 1
        matched += 1
    print(f"對上產業分類的個股 {matched} 檔")

    rows = []
    for name, a in agg.items():
        net = a["net"] / UNIT
        if abs(net) < MIN_INDUSTRY_FLOW:
            continue
        rows.append({
            "name": name,
            "sector": a["sector"],
            "net": round(net, 2),
            "chg": round(a["chgw"] / a["w"], 2) if a["w"] else 0.0,
        })
    rows.sort(key=lambda r: -r["net"])
    if len(rows) < 2:
        print("有效產業不足兩個，不更新檔案。")
        return 0

    inflow = sum(r["net"] for r in rows if r["net"] > 0)
    outflow = -sum(r["net"] for r in rows if r["net"] < 0)
    pair = min(inflow, outflow)
    # 殘差落在比較大的那一側：流入大於流出時是找不到來源的流入，
    # 反之則是找不到去處的流出。兩種情況真實資料都會出現。
    base = max(inflow, outflow)
    unm = (base - pair) / base * 100 if base else 0

    payload = {
        "meta": {
            "market": "台股 上市",
            "unit": "億元",
            "snapshot": iso,
            "freq": "收盤後更新",
            "source": "證交所 T86 · 外資＋投信買賣超股數 × 當日均價",
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "industries": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))

    # 同時存一份到 hist/，歷史就會隨著每天的排程自己累積
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(os.path.join(HIST_DIR, f"{iso}.json"), "w", encoding="utf-8") as fp:
        json.dump({"date": iso, "industries": rows}, fp,
                  ensure_ascii=False, separators=(",", ":"))

    print(f"產業數 {len(rows)}｜流入 {inflow:.2f}｜流出 -{outflow:.2f}"
          f"｜可配對 {pair:.2f}｜未配對 {unm:.0f}%（單位：億元）")
    print(f"已寫入 {os.path.normpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
