"""ATS (Against The Spread) reality check.

The dashboard shows OUTRIGHT WIN rates for our strategies. But when the
strategy says 'Handicap' / 'Spread' play (i.e., heavy favourite), the actual
question is: does the team COVER the spread?

This is different from 'do they win'. A heavy favourite can win the game
but lose your handicap bet if they didn't win by enough points.

We don't have historical 22bet spread lines, so we estimate the 'fair' spread
from our model:
   fair_spread = exp_h_score - exp_a_score
This is the spread a sharp market WOULD set if it only knew rolling stats.
22bet's actual spreads are usually within 1-2 points of this.

ATS_WR = % of bets where (actual_margin > model_spread) when betting on the
favorite, OR (actual_margin < model_spread) when betting on the underdog.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data")


def build_features(df):
    """Same as multi_league/backtest.build_features but also outputs expected spread."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["game_idx"] = range(len(df))

    h = df.rename(columns={"home_team":"team","away_team":"opp",
                            "home_score":"team_score","away_score":"opp_score"}).copy()
    h["home"] = True
    a = df.rename(columns={"away_team":"team","home_team":"opp",
                            "away_score":"team_score","home_score":"opp_score"}).copy()
    a["home"] = False
    ts = pd.concat([h, a], ignore_index=True)
    ts["diff"]  = ts["team_score"] - ts["opp_score"]
    ts["pts_for"] = ts["team_score"]
    ts["pts_against"] = ts["opp_score"]
    ts["win"]   = (ts["diff"] > 0).astype(int)
    ts = ts.sort_values(["team","date","game_idx"]).reset_index(drop=True)

    season_grp = ts.groupby(["team","season"])
    def roll(col, w, min_p=None):
        if min_p is None: min_p = max(3, w//3)
        return season_grp[col].transform(lambda x: x.shift(1).rolling(w, min_periods=min_p).mean())

    ts["r_wr"]    = roll("win", 10)
    ts["r_diff"]  = roll("diff", 10)
    ts["r_pts_for"]     = roll("pts_for", 10)
    ts["r_pts_against"] = roll("pts_against", 10)
    ts["r5_wr"]   = roll("win", 5, 2)
    ts["r5_diff"] = roll("diff", 5, 2)
    ts["r20_wr"]  = roll("win", 20, 5)
    ts["r3_wr"]   = roll("win", 3, 2)

    ts["prev_date"] = season_grp["date"].transform(lambda x: x.shift(1))
    ts["rest"] = ((ts["date"] - ts["prev_date"]).dt.days
                  .fillna(7).clip(0, 14).astype(int))
    ts["season_wins"]   = season_grp["win"].transform(lambda x: x.shift(1).cumsum()).fillna(0)
    ts["season_losses"] = season_grp["win"].transform(lambda x: (1 - x).shift(1).cumsum()).fillna(0)
    ts["szn_pct"] = ts["season_wins"] / (ts["season_wins"] + ts["season_losses"] + 1e-6)

    keep = ["game_idx","team","r_wr","r_diff","r_pts_for","r_pts_against",
            "r5_wr","r5_diff","r20_wr","r3_wr","rest","szn_pct","home"]
    home = ts[ts["home"]][keep].rename(columns={c: f"h_{c}" for c in keep if c not in ("game_idx","home")})
    away = ts[~ts["home"]][keep].rename(columns={c: f"a_{c}" for c in keep if c not in ("game_idx","home")})
    home = home.drop(columns=["home"])
    away = away.drop(columns=["home"])
    g = df.merge(home, on="game_idx").merge(away, on="game_idx")
    g["home_win"]  = (g["home_score"] > g["away_score"]).astype(int)
    g["margin"]    = g["home_score"] - g["away_score"]
    # Model-estimated spread (home perspective): expected margin
    g["exp_h_score"] = (g["h_r_pts_for"] + g["a_r_pts_against"]) / 2
    g["exp_a_score"] = (g["a_r_pts_for"] + g["h_r_pts_against"]) / 2
    g["exp_margin"]  = g["exp_h_score"] - g["exp_a_score"]
    # Home covers if actual margin BEATS expected margin
    g["home_covers"] = (g["margin"] > g["exp_margin"]).astype(int)

    g = g.dropna(subset=["h_r_wr","a_r_wr","h_r_diff","a_r_diff","exp_margin"])
    g["month"] = g["date"].dt.month
    g["season_month"] = ((g["month"] - 10) % 12) + 1
    g["early_season"] = g["season_month"].between(1, 2)
    g["mid_season"]   = g["season_month"].between(3, 6)
    g["late_season"]  = g["season_month"].between(7, 9)
    return g


def run_strats(g):
    """For each strategy, compute BOTH outright WR and ATS WR."""
    TRUE = pd.Series(True, index=g.index)
    FALSE = pd.Series(False, index=g.index)
    out = []

    def bt(sid, name, sig, bet_home=TRUE):
        df = g[sig]
        n = len(df)
        if n < 30: return
        bh = (bet_home[sig] if hasattr(bet_home, '__len__') else bet_home).reindex(df.index)
        outright_correct = (bh & (df["home_win"] == 1)) | (~bh & (df["home_win"] == 0))
        ats_correct      = (bh & (df["home_covers"] == 1)) | (~bh & (df["home_covers"] == 0))
        out.append({
            "Strategy":  f"{sid} {name}",
            "Bets":      n,
            "OutrightWR": round(outright_correct.sum() / n * 100, 1),
            "ATS_WR":    round(ats_correct.sum() / n * 100, 1),
            "Gap":       round((outright_correct.sum() - ats_correct.sum()) / n * 100, 1),
        })

    bt("S5",  "Home Form Edge +5",          (g["h_r_diff"] - g["a_r_diff"] > 5) & (g["h_r_diff"] > 3))
    bt("S6",  "Dominant Away vs Losing Home", (g["a_r_diff"] > 7) & (g["h_r_diff"] < -2), bet_home=FALSE)
    bt("S7",  "Rest + Season Lead",         (g["h_rest"] - g["a_rest"] >= 2) & (g["h_szn_pct"] - g["a_szn_pct"] > 0.10))
    bt("S8",  "Hot Home vs Cold Away (5g)", (g["h_r5_wr"] > 0.70) & (g["a_r5_wr"] < 0.35))
    bt("S9",  "Triple-Edge Home",           (g["h_rest"] - g["a_rest"] >= 1) & (g["h_r_wr"] - g["a_r_wr"] > 0.15) & (g["h_r_diff"] - g["a_r_diff"] > 3))
    bt("S11", "Form Gap >8",                (g["h_r_diff"] - g["a_r_diff"]).abs() > 8, bet_home=(g["h_r_diff"] - g["a_r_diff"] > 0))
    bt("S13", "Mild Form Edge",             (g["h_r_diff"] - g["a_r_diff"] > 2.5) & (g["h_r_diff"] > 1))
    bt("S14", "Home Consistency",           (g["h_r_diff"] > 5) & (g["h_r5_diff"] > 5))
    bt("S15", "20g Hot Home",               (g["h_r20_wr"] > 0.65) & (g["h_r_wr"] - g["a_r_wr"] > 0.10))
    bt("S16", "Mid-Season Form + Szn Lead", g["mid_season"] & (g["h_r_diff"] - g["a_r_diff"] > 5) & (g["h_szn_pct"] - g["a_szn_pct"] > 0.05))
    bt("S20", "Late-Season Edge >15%",      g["late_season"] & ((g["h_szn_pct"] - g["a_szn_pct"]).abs() > 0.15), bet_home=(g["h_szn_pct"] > g["a_szn_pct"]))
    bt("S4",  "Win-Rate Gap >25%",          (g["h_r_wr"] - g["a_r_wr"]).abs() > 0.25, bet_home=(g["h_r_wr"] - g["a_r_wr"] > 0))

    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n=== OUTRIGHT WIN RATE vs ATS WIN RATE — across leagues ===")
    print("\nKey: ATS_WR = % of bets where the team ALSO covered the model spread")
    print("     Gap    = how many percentage points lower ATS_WR is than outright WR")
    print("     Break-even at -110 odds = 52.4%\n")

    # Pick representative leagues
    leagues = [
        ("Euroleague", "Euroleague"),
        ("NBA-equivalent: use Spain ACB", "Spain_ACB"),
        ("Turkey BSL", "Turkey_BSL"),
        ("Germany BBL", "Germany_BBL"),
        ("France LNB", "France_LNB"),
    ]

    overall = []
    for league_label, folder in leagues:
        f = ROOT / folder / "games_all.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f)
        df = df[df["gameType"] == "Regular Season"] if "gameType" in df else df
        if len(df) < 100: continue
        g = build_features(df)
        if len(g) < 100: continue
        print(f"\n>>> {league_label} ({len(g):,} backtestable games)")
        print(f"  {'Strategy':<32}{'Bets':>6}{'Outright%':>11}{'ATS%':>8}{'Gap':>6}  Verdict")
        for r in run_strats(g):
            ats_profitable = r["ATS_WR"] >= 52.4
            outright_profitable = r["OutrightWR"] >= 52.4
            verdict = "ATS PROFITABLE" if ats_profitable else "ATS LOSES MONEY"
            print(f"  {r['Strategy']:<32}{r['Bets']:>6}{r['OutrightWR']:>10.1f}%{r['ATS_WR']:>7.1f}%{r['Gap']:>5.1f}  {verdict}")
            r["League"] = league_label
            overall.append(r)

    # Summary table
    print("\n\n=== SUMMARY: AVERAGE ATS WR ACROSS ALL TESTED LEAGUES ===\n")
    if overall:
        df = pd.DataFrame(overall)
        agg = df.groupby("Strategy").agg(
            leagues=("Bets", "count"),
            avg_outright=("OutrightWR", "mean"),
            avg_ats=("ATS_WR", "mean"),
            avg_gap=("Gap", "mean"),
            total_bets=("Bets", "sum"),
        ).round(1).sort_values("avg_ats", ascending=False)
        print(agg.to_string())

        print("\n\n=== INTERPRETATION ===")
        ats_profitable = agg[agg["avg_ats"] >= 52.4]
        ats_marginal   = agg[(agg["avg_ats"] >= 50) & (agg["avg_ats"] < 52.4)]
        ats_losing     = agg[agg["avg_ats"] < 50]
        print(f"  Strategies with ATS_WR >= 52.4% (break-even):  {len(ats_profitable)}")
        print(f"  Strategies marginal (50.0% - 52.4%):             {len(ats_marginal)}")
        print(f"  Strategies losing money on ATS:                  {len(ats_losing)}")
        print()
        if len(ats_profitable) > 0:
            print("  Safest for HANDICAP bets:")
            for sid, row in ats_profitable.head(5).iterrows():
                print(f"    {sid}  outright={row['avg_outright']}% / ATS={row['avg_ats']}% ({int(row['total_bets'])} total bets)")
        if len(ats_losing) > 0:
            print("\n  AVOID for HANDICAP (use moneyline only):")
            for sid, row in ats_losing.head(5).iterrows():
                print(f"    {sid}  outright={row['avg_outright']}% but ATS only {row['avg_ats']}%")


if __name__ == "__main__":
    main()
