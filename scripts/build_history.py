#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/hist/ 底下的每日檔壓成一個 data/history.json，給頁面算
「N 日累積 / 持續性 / 族群強弱」用。

用緊湊格式：產業名稱只出現一次，每天只存索引與數字，檔案小很多。

    python scripts/build_history.py --days 30
"""
import argparse, json, os
from glob import glob

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "..", "data", "hist")
OUT = os.path.join(HERE, "..", "data", "history_tw.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="保留最近幾個交易日")
    a = ap.parse_args()

    files = sorted(glob(os.path.join(HIST, "*.json")))[-a.days:]
    if not files:
        print("data/hist/ 是空的，先跑 collect_history.py")
        return 0

    names, sectors, index = [], [], {}
    days = []
    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        row = []
        for r in d["industries"]:
            n = r["name"]
            if n not in index:
                index[n] = len(names)
                names.append(n)
                sectors.append(r["sector"])
            row.append([index[n], round(r["net"], 2), round(r["chg"], 2)])
        days.append({"d": d["date"], "r": row})

    payload = {"names": names, "sectors": sectors, "days": days}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT) / 1024
    print(f"{len(days)} 個交易日、{len(names)} 個產業 → {os.path.normpath(OUT)}（{size:.0f} KB）")
    print(f"期間 {days[0]['d']} ~ {days[-1]['d']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
