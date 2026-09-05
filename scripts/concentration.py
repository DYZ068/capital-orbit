#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
算出每個產業的「集中度」：前 5 大同方向個股，佔該產業淨額的比重。

為什麼需要這支：
「產業資金流」這個詞會讓人以為是整個產業在動，但很多時候那個數字
其實是兩三檔股票造成的。集中度就是用來分辨這兩件事的。

  集中度 < 55%   全面輪動 —— 真的是產業層級的事，可以用產業邏輯去找同業
  集中度 55~80%  混合 —— 有龍頭帶頭，但同業有跟上
  集中度 > 80%   個股事件 —— 這不是產業訊號，是那幾檔的新聞

超過 100% 代表同方向個股的總和比產業淨額還大，也就是產業內部有人在對做，
方向分歧嚴重。這種情況下「產業淨流入」這個數字本身就沒什麼意義。

    python scripts/concentration.py
    python scripts/concentration.py --top 12
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_tw as T  # noqa: E402


def label(conc):
    if conc >= 100:
        return "★方向分歧"
    if conc >= 80:
        return "★個股事件"
    if conc >= 55:
        return "混合"
    return "全面輪動"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=8, help="流入／流出各列幾個產業")
    a = ap.parse_args()

    day_rows = T.fetch_json(T.DAY_ALL)
    iso, ymd = T.roc_to_iso(day_rows[0]["Date"])
    price = {}
    for r in day_rows:
        vol, val = T.num(r.get("TradeVolume")), T.num(r.get("TradeValue"))
        close = T.num(r.get("ClosingPrice"))
        avg = val / vol if vol > 0 else close
        if avg > 0:
            price[r["Code"]] = (avg, r.get("Name", "").strip())

    t86 = T.fetch_json(T.T86, date=ymd, selectType="ALL", response="json")
    f = t86["fields"]
    ic, i1, i2, i3 = (f.index("證券代號"),
                      f.index("外陸資買賣超股數(不含外資自營商)"),
                      f.index("外資自營商買賣超股數"),
                      f.index("投信買賣超股數"))
    company = json.load(open(T.CAT_CACHE, encoding="utf-8")) \
        if os.path.exists(T.CAT_CACHE) else \
        {r["公司代號"]: r["產業別"].strip() for r in T.fetch_json(T.COMPANY)}

    byind = defaultdict(list)
    for row in t86["data"]:
        c = str(row[ic]).strip()
        cat = company.get(c)
        if not cat or cat not in T.INDUSTRY or c not in price:
            continue
        sh = T.num(row[i1]) + T.num(row[i2]) + T.num(row[i3])
        avg, name = price[c]
        byind[T.INDUSTRY[cat][0]].append((name, sh * avg / 1e8))

    res = []
    for ind, lst in byind.items():
        net = sum(v for _, v in lst)
        if abs(net) < 0.5:
            continue
        same = sorted((v for _, v in lst if (v > 0) == (net > 0)), key=abs, reverse=True)
        conc = abs(sum(same[:5]) / net) * 100 if net else 0.0
        names = [n for n, v in sorted(lst, key=lambda x: -abs(x[1]))
                 if (v > 0) == (net > 0)][:3]
        res.append((ind, net, conc, len(lst), names))
    res.sort(key=lambda r: -r[1])

    print(f"\n{iso}   集中度 = 前 5 大同方向個股 ÷ 產業淨額\n")
    print(f"{'產業':<12}{'淨額(億)':>10}{'集中度':>8}{'檔數':>6}   {'前三大貢獻者':<24}判讀")
    print("─" * 84)

    def show(rows):
        for ind, net, conc, n, names in rows:
            print(f"{ind:<12}{net:>10.1f}{conc:>7.0f}%{n:>6}   "
                  f"{'、'.join(names):<24}{label(conc)}")

    show(res[:a.top])
    if len(res) > a.top * 2:
        print(f"{'':<12}{'⋮':>10}")
    show(res[-a.top:])
    print()
    print("判讀方式：集中度高的產業，不要用「產業輪動」去解釋，")
    print("          直接去研究那幾檔的新聞與基本面才對。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
