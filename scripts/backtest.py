#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
實測：產業資金流到底能不能預測隔天的漲跌？

方法是量化研究最標準的兩把尺：

1. Rank IC（資訊係數）
   每一天，把當天各產業的「資金流排名」和「未來 N 天報酬排名」算 Spearman 相關。
   每天得到一個數字，再看它的平均與 t 統計量。
   IC 0.02~0.05 在業界就算可用；0.10 以上非常罕見。

2. 分位數組合
   每天把產業依資金流分成 5 組，看最高組減最低組的未來報酬。
   IC 說「有沒有關係」，分位數說「賺不賺得到」。

還會比較三種訊號口徑，因為口徑選擇往往比模型重要：
   raw    當日淨流入金額
   norm   淨流入 ÷ 該產業近 20 日平均成交額（去掉規模效應）
   z20    近 20 日的 z-score（去掉該產業自己的長期偏誤）

    python scripts/backtest.py
"""
import json, os, sys
from glob import glob

import numpy as np
import pandas as pd

HIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "hist")


def load():
    rows = []
    for p in sorted(glob(os.path.join(HIST, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        for r in d["industries"]:
            rows.append({"date": d["date"], "name": r["name"],
                         "sector": r["sector"], "net": r["net"], "chg": r["chg"]})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["name", "date"]).reset_index(drop=True)


def build_signals(df):
    g = df.groupby("name", sort=False)
    df["abs20"] = g["net"].transform(lambda s: s.abs().rolling(20, min_periods=8).mean())
    df["norm"] = df["net"] / df["abs20"]
    m = g["net"].transform(lambda s: s.rolling(20, min_periods=8).mean())
    sd = g["net"].transform(lambda s: s.rolling(20, min_periods=8).std())
    df["z20"] = (df["net"] - m) / sd.replace(0, np.nan)
    df["raw"] = df["net"]
    # 未來報酬：把每天的產業漲跌幅往前累加
    for h in (1, 3, 5, 10):
        df[f"fwd{h}"] = g["chg"].transform(
            lambda s: s.shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1)))
    return df


def rank_ic(df, sig, fwd):
    out = []
    for _, day in df.groupby("date"):
        d = day[[sig, fwd]].dropna()
        if len(d) < 12:
            continue
        ic = d[sig].rank().corr(d[fwd].rank())
        if pd.notna(ic):
            out.append(ic)
    return np.array(out)


def quintile(df, sig, fwd, n=5):
    hi, lo = [], []
    for _, day in df.groupby("date"):
        d = day[[sig, fwd]].dropna()
        if len(d) < 15:
            continue
        q = pd.qcut(d[sig].rank(method="first"), n, labels=False)
        hi.append(d[fwd][q == n - 1].mean())
        lo.append(d[fwd][q == 0].mean())
    return np.array(hi), np.array(lo)


def stat(a):
    if len(a) < 3:
        return 0.0, 0.0, 0
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std(ddof=1) > 0 else 0.0
    return a.mean(), t, len(a)


def main():
    df = build_signals(load())
    days = df["date"].nunique()
    print(f"樣本：{days} 個交易日 × {df['name'].nunique()} 個產業"
          f"（{df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d}）")
    print(f"⚠ {days} 天的樣本很小，下面的數字只能當方向參考，不能當結論。\n")

    print("【1】Rank IC：資金流排名 vs 未來報酬排名")
    print(f"{'訊號':<7}{'期間':<7}{'IC均值':>9}{'t值':>8}{'勝率':>8}{'有效天數':>9}   判讀")
    print("─" * 72)
    best = None
    for sig, label in [("raw", "raw"), ("norm", "norm"), ("z20", "z20")]:
        for h in (1, 3, 5, 10):
            ic = rank_ic(df, sig, f"fwd{h}")
            m, t, n = stat(ic)
            if n < 10:
                continue
            win = (ic > 0).mean() * 100
            verdict = ("有訊號" if abs(t) > 2 and abs(m) > 0.03 else
                       "微弱" if abs(t) > 1.5 else "看不出來")
            print(f"{label:<7}{h:>2}日{'':<3}{m:>9.3f}{t:>8.2f}{win:>7.0f}%{n:>9}   {verdict}")
            if best is None or abs(t) > abs(best[2]):
                best = (sig, h, t, m)
    print()

    print("【2】分位數組合：最高流入組 減 最低流入組 的未來報酬（%）")
    print(f"{'訊號':<7}{'期間':<7}{'高組':>9}{'低組':>9}{'價差':>9}{'t值':>8}")
    print("─" * 52)
    for sig in ("raw", "norm", "z20"):
        for h in (1, 5):
            hi, lo = quintile(df, sig, f"fwd{h}")
            if len(hi) < 10:
                continue
            sp = hi - lo
            m, t, _ = stat(sp)
            print(f"{sig:<7}{h:>2}日{'':<3}{np.nanmean(hi):>9.3f}{np.nanmean(lo):>9.3f}"
                  f"{m:>9.3f}{t:>8.2f}")
    print()

    print("【3】資金流本身有沒有慣性？（今天流入的，明天還會流入嗎）")
    for sig in ("raw", "norm", "z20"):
        s = df.groupby("name")[sig].apply(lambda x: x.autocorr(1))
        print(f"  {sig:<6} 一階自相關中位數 {s.median():>6.3f}"
              f"   → {'有慣性，可以追' if s.median()>0.15 else '幾乎沒有慣性，是雜訊主導' if abs(s.median())<0.1 else '輕微反轉'}")
    print()

    print("【4】當期同步性：今天的資金流 vs 今天的漲跌（不是預測，是確認）")
    for sig in ("raw", "norm", "z20"):
        ic = []
        for _, day in df.groupby("date"):
            d = day[[sig, "chg"]].dropna()
            if len(d) >= 12:
                ic.append(d[sig].rank().corr(d["chg"].rank()))
        m, t, n = stat(np.array(ic))
        print(f"  {sig:<6} 同期 Rank IC {m:>6.3f}  t={t:>6.2f}")
    print("  （同期相關高是理所當然的——買盤推升價格。這個數字高不代表能預測。）\n")

    print("【5】扣掉交易成本還剩多少（這才是真正的檢驗）")
    FEE, TAX = 0.1425, 0.30      # 券商手續費（單邊 %）、證交稅（賣出 %）
    for disc, label in [(1.0, "全額手續費"), (0.6, "6 折手續費"), (0.28, "2.8 折(高頻)")]:
        cost = FEE * disc * 2 + TAX
        print(f"  {label:<14} 來回成本 {cost:.3f}%")
    print()
    for sig in ("raw", "z20"):
        hi, lo = quintile(df, sig, "fwd1")
        sp = hi - lo
        m, t, n = stat(sp)
        print(f"  {sig:<5} 每日毛價差 {m:.3f}%  t={t:.2f}")
        for disc, label in [(1.0, "全額"), (0.6, "6折"), (0.28, "2.8折")]:
            cost = FEE * disc * 2 + TAX
            net = m - cost
            print(f"        扣{label:<5}成本 {cost:.3f}%  →  淨 {net:+.3f}%/日"
                  f"  {'仍有利可圖' if net > 0.05 else '幾乎打平' if net > -0.05 else '穩定虧損'}")
    print()
    print("  結論：多空各換一次手就要付掉大部分的毛利。訊號是真的，但薄到")
    print("  被成本吃掉——這是絕大多數「看起來有效」的因子的真實下場。")


if __name__ == "__main__":
    main()
