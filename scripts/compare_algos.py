#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把三種配對演算法跑在同一份資料上，量化它們的差異。

這支腳本存在的理由：頁面上切換演算法時，排行榜完全不變，只有弧線變。
很容易誤以為「三個演算法沒差」。實際上差的是**資金移動的敘事**，
這裡把那個敘事用數字攤開來看。

    python scripts/compare_algos.py data/tw.json
"""
import json
import sys
from collections import defaultdict


def match(rows, algo):
    OUT = [(r["name"], -r["net"], r["sector"]) for r in rows if r["net"] < 0]
    INN = [(r["name"], r["net"], r["sector"]) for r in rows if r["net"] > 0]
    SO = sum(x[1] for x in OUT)
    SI = sum(x[1] for x in INN)
    M = min(SO, SI)
    if not M:
        return [], M
    o = [[n, c * M / SO, s] for n, c, s in OUT]
    i = [[n, c * M / SI, s] for n, c, s in INN]
    eps = M * 1e-9
    flows = []

    if algo == "gravity":
        for a in o:
            for b in i:
                v = a[1] * b[1] / M
                if v > M * 1e-6:
                    flows.append((a[0], b[0], v, a[2], b[2]))
    elif algo == "transport":
        pairs = sorted((0.25 if a[2] == b[2] else 1.0, ai, bi)
                       for ai, a in enumerate(o) for bi, b in enumerate(i))
        for _, ai, bi in pairs:
            v = min(o[ai][1], i[bi][1])
            if v <= eps:
                continue
            o[ai][1] -= v
            i[bi][1] -= v
            flows.append((o[ai][0], i[bi][0], v, o[ai][2], i[bi][2]))
    else:  # greedy
        oi = sorted(range(len(o)), key=lambda k: -o[k][1])
        ii = sorted(range(len(i)), key=lambda k: -i[k][1])
        p = q = 0
        while p < len(oi) and q < len(ii):
            a, b = oi[p], ii[q]
            v = min(o[a][1], i[b][1])
            if v > eps:
                o[a][1] -= v
                i[b][1] -= v
                flows.append((o[a][0], i[b][0], v, o[a][2], i[b][2]))
            if o[a][1] <= eps:
                p += 1
            if i[b][1] <= eps:
                q += 1
    return flows, M


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/tw.json"
    d = json.load(open(path, encoding="utf-8"))
    rows = [r for r in d["industries"] if r["net"]]
    unit = d["meta"].get("unit", "億元")
    print(f"{d['meta'].get('market','?')}  {d['meta'].get('snapshot','?')}  "
          f"{len(rows)} 個產業  單位：{unit}\n")

    per_node = {}
    print(f"{'演算法':<8}{'弧線數':>7}{'配對總量':>11}{'最大一條':>11}"
          f"{'前5條佔比':>11}{'同族群佔比':>12}")
    print("─" * 62)

    for algo, label in [("greedy", "貪婪"), ("gravity", "重力"), ("transport", "運輸")]:
        flows, M = match(rows, algo)
        total = sum(f[2] for f in flows)
        big = sorted(flows, key=lambda f: -f[2])
        top5 = sum(f[2] for f in big[:5]) / total * 100 if total else 0
        same = sum(f[2] for f in flows if f[3] == f[4]) / total * 100 if total else 0
        print(f"{label:<8}{len(flows):>7}{total:>10.2f}{'':1}"
              f"{big[0][2] if big else 0:>10.2f}{'':1}{top5:>10.1f}%{same:>11.1f}%")

        agg = defaultdict(float)
        for a, b, v, _, _ in flows:
            agg[a] += v
            agg[b] += v
        per_node[algo] = dict(agg)

    print("\n每個產業配到的總量，三種演算法是否相同：")
    ref = per_node["greedy"]
    for algo, label in [("gravity", "重力"), ("transport", "運輸")]:
        worst = max((abs(per_node[algo].get(k, 0) - v) for k, v in ref.items()), default=0)
        print(f"  貪婪 vs {label}：最大差異 {worst:.2e} {unit}"
              f"  → {'完全相同' if worst < 1e-6 else '不同'}")

    print("\n各演算法排前三大的軌跡（同一份資料，敘事完全不一樣）：")
    for algo, label in [("greedy", "貪婪"), ("gravity", "重力"), ("transport", "運輸")]:
        flows, _ = match(rows, algo)
        print(f"\n  【{label}】")
        for a, b, v, sa, sb in sorted(flows, key=lambda f: -f[2])[:3]:
            tag = "同族群" if sa == sb else "跨族群"
            print(f"    {a} → {b}   {v:.2f}{unit}   ({sa}→{sb} {tag})")


if __name__ == "__main__":
    main()
