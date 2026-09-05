#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓歷史的產業資金流，存成每日一檔，給回測用。

    python scripts/collect_history.py --days 90

證交所兩支有日期參數的端點：
  T86       三大法人買賣超日報
  MI_INDEX  每日收盤行情（type=ALLBUT0999 是「全部（不含權證）」）

已經抓過的日期會跳過，中斷了再跑一次就好。
"""
import argparse, json, os, sys, time
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_tw as T  # noqa: E402
import requests

HIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "hist")
MI = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
PAUSE = 1.8


def get(url, **params):
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.exceptions.RequestException:
            time.sleep(4 * (attempt + 1)); continue
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            time.sleep(4 * (attempt + 1)); continue
        return r.json()
    return None


def one_day(ymd, company):
    t86 = get(T.T86, date=ymd, selectType="ALL", response="json")
    if not t86 or t86.get("stat") != "OK" or not t86.get("data"):
        return None
    time.sleep(PAUSE)
    mi = get(MI, date=ymd, type="ALLBUT0999", response="json")
    if not mi or mi.get("stat") != "OK":
        return None
    tab = next((t for t in (mi.get("tables") or []) if "證券代號" in t.get("fields", [])), None)
    if not tab:
        return None

    f = tab["fields"]
    ic, iv, ia, icl, isg, isp = (f.index("證券代號"), f.index("成交股數"), f.index("成交金額"),
                                 f.index("收盤價"), f.index("漲跌(+/-)"), f.index("漲跌價差"))
    price = {}
    for row in tab["data"]:
        vol, val, close = T.num(row[iv]), T.num(row[ia]), T.num(row[icl])
        if vol <= 0 or close <= 0:
            continue
        sign = -1 if "-" in str(row[isg]) else 1
        chg = sign * T.num(row[isp])
        prev = close - chg
        price[str(row[ic]).strip()] = (val / vol, (chg / prev * 100) if prev > 0 else 0.0)

    g = t86["fields"]
    jc, jf, jd, jt = (g.index("證券代號"), g.index("外陸資買賣超股數(不含外資自營商)"),
                      g.index("外資自營商買賣超股數"), g.index("投信買賣超股數"))
    agg = defaultdict(lambda: {"net": 0.0, "w": 0.0, "chgw": 0.0, "sector": ""})
    for row in t86["data"]:
        c = str(row[jc]).strip()
        cat = company.get(c)
        if not cat or cat not in T.INDUSTRY or c not in price:
            continue
        sh = T.num(row[jf]) + T.num(row[jd]) + T.num(row[jt])
        avg, chg = price[c]
        flow = sh * avg
        name, sector = T.INDUSTRY[cat]
        a = agg[name]; a["sector"] = sector
        a["net"] += flow; a["w"] += abs(flow); a["chgw"] += chg * abs(flow)
    if len(agg) < 5:
        return None
    return {"industries": [{"name": k, "sector": v["sector"],
                            "net": round(v["net"] / 1e8, 4),
                            "chg": round(v["chgw"] / v["w"], 4) if v["w"] else 0.0}
                           for k, v in agg.items()]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="往回幾個日曆天")
    a = ap.parse_args()
    os.makedirs(HIST, exist_ok=True)
    company = json.load(open(T.CAT_CACHE, encoding="utf-8"))

    got = skipped = missing = 0
    d = date.today()
    for _ in range(a.days):
        d -= timedelta(days=1)
        if d.weekday() > 4:
            continue
        ymd = d.strftime("%Y%m%d")
        path = os.path.join(HIST, f"{d.isoformat()}.json")
        if os.path.exists(path):
            skipped += 1; continue
        res = one_day(ymd, company)
        if res is None:
            missing += 1
            print(f"  {d}  無資料（休市或抓取失敗）")
        else:
            res["date"] = d.isoformat()
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(res, fp, ensure_ascii=False, separators=(",", ":"))
            got += 1
            tot = sum(r["net"] for r in res["industries"] if r["net"] > 0)
            print(f"  {d}  {len(res['industries'])} 產業，流入 {tot:>8.1f} 億")
        time.sleep(PAUSE)
    print(f"\n新增 {got} 天，已存在 {skipped} 天，沒資料 {missing} 天")
    print(f"目前累積 {len([x for x in os.listdir(HIST) if x.endswith('.json')])} 個交易日")


if __name__ == "__main__":
    main()
