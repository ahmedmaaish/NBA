"""Multi-league backtester — runs the 22 strategies against each league's
games_all.csv. Outputs per-league CSV results so you can see which
strategies generalize beyond NBA.

Schema expected:
    date, season, league, gameType, home_team, away_team, home_score, away_score

Usage:
    python -m scanner.multi_league.backtest
    python -m scanner.multi_league.backtest --league "Euroleague"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import numpy as np

from .base import ROOT


N_GAMES      = 10
MIN_BETS     = 30
WIN_PAYOUT   = 100 / 110
BREAKEVEN_WR = 52.38


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build team-level features from a single league's games_all.csv.

    Splits each game into 2 team-rows (one for home, one for away), builds
    rolling features per team, then merges back into one row per game.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["game_idx"] = range(len(df))

    # Explode into team-perspective rows
    h = df.rename(columns={"home_team":"team","away_team":"opp",
                            "home_score":"team_score","away_score":"opp_score"}).copy()
    h["home"] = True
    a = df.rename(columns={"away_team":"team","home_team":"opp",
                            "away_score":"team_score","home_score":"opp_score"}).copy()
    a["home"] = False
    ts = pd.concat([h, a], ignore_index=True)
    ts["diff"]   = ts["team_score"] - ts["opp_score"]
    ts["win"]    = (ts["diff"] > 0).astype(int)
    ts = ts.sort_values(["team","date","game_idx"]).reset_index(drop=True)

    # CRITICAL: rolling windows must be PER (team, season) so that the rolling
    # stat of a team's first game of season N doesn't pull in season N-1 games.
    # Verified by scanner/multi_league/verify.py — without this, ~30% of samples
    # mismatch the manual same-season calculation.
    season_grp = ts.groupby(["team", "season"])
    def roll(col, w, min_p=None):
        if min_p is None:
            min_p = max(3, w // 3)
        return season_grp[col].transform(
            lambda x: x.shift(1).rolling(w, min_periods=min_p).mean()
        )

    ts["r_wr"]      = roll("win", 10)
    ts["r_diff"]    = roll("diff", 10)
    ts["r5_wr"]     = roll("win", 5, min_p=2)
    ts["r5_diff"]   = roll("diff", 5, min_p=2)
    ts["r3_wr"]     = roll("win", 3, min_p=2)
    ts["r20_wr"]    = roll("win", 20, min_p=5)

    # Days rest is computed within season (don't carry over from previous season)
    ts["prev_date"] = season_grp["date"].transform(lambda x: x.shift(1))
    ts["rest"] = ((ts["date"] - ts["prev_date"]).dt.days
                  .fillna(7).clip(0, 14).astype(int))

    # Cumulative season W/L per team (PRE-game, season-scoped)
    ts["season_wins"]   = season_grp["win"].transform(lambda x: x.shift(1).cumsum()).fillna(0)
    ts["season_losses"] = season_grp["win"].transform(lambda x: (1 - x).shift(1).cumsum()).fillna(0)
    ts["szn_pct"]       = ts["season_wins"] / (ts["season_wins"] + ts["season_losses"] + 1e-6)

    # Re-merge into per-game rows (home + away features side-by-side)
    keep = ["game_idx","team","r_wr","r_diff","r5_wr","r5_diff","r3_wr","r20_wr",
            "rest","szn_pct","home"]
    home_feats = ts[ts["home"]][keep].rename(columns={c: f"h_{c}" for c in keep if c not in ("game_idx","home")})
    away_feats = ts[~ts["home"]][keep].rename(columns={c: f"a_{c}" for c in keep if c not in ("game_idx","home")})
    home_feats = home_feats.drop(columns=["home"])
    away_feats = away_feats.drop(columns=["home"])
    g = df.merge(home_feats, on="game_idx", how="left").merge(away_feats, on="game_idx", how="left")
    g["home_win"] = (g["home_score"] > g["away_score"]).astype(int)

    # Drop early games without enough history
    g = g.dropna(subset=["h_r_wr","a_r_wr","h_r_diff","a_r_diff"])

    # Date-derived season position
    g["month"] = g["date"].dt.month
    g["season_month"] = ((g["month"] - 10) % 12) + 1
    g["early_season"] = g["season_month"].between(1, 2)
    g["mid_season"]   = g["season_month"].between(3, 6)
    g["late_season"]  = g["season_month"].between(7, 9)

    return g


def run_strategies(g: pd.DataFrame) -> list[dict]:
    TRUE  = pd.Series(True,  index=g.index)
    results = []

    def bt(name, sig, bet_home=TRUE):
        df = g[sig].copy()
        n = len(df)
        if n < MIN_BETS:
            return
        bh = (bet_home[sig] if hasattr(bet_home, '__len__') else bet_home).reindex(df.index)
        correct = (bh & (df["home_win"] == 1)) | (~bh & (df["home_win"] == 0))
        wins = int(correct.sum())
        pnl  = correct.map({True: WIN_PAYOUT, False: -1.0})
        results.append({
            "Strategy": name,
            "Bets":     n,
            "WinRate%": round(wins / n * 100, 2),
            "ROI%":     round(pnl.sum() / n * 100, 2),
            "PnL_u":    round(pnl.sum(), 2),
        })

    bt("S1  Away B2B + Home Rested",          (g["a_rest"] == 1) & (g["h_rest"] >= 2))
    bt("S5  Home Form Edge +5",               (g["h_r_diff"] - g["a_r_diff"] > 5) & (g["h_r_diff"] > 3))
    bt("S6  Dominant Away vs Losing Home",    (g["a_r_diff"] > 7) & (g["h_r_diff"] < -2), bet_home=pd.Series(False, index=g.index))
    bt("S7  Rest + Season Lead",              (g["h_rest"] - g["a_rest"] >= 2) & (g["h_szn_pct"] - g["a_szn_pct"] > 0.10))
    bt("S8  Hot Home vs Cold Away (5g)",      (g["h_r5_wr"] > 0.70) & (g["a_r5_wr"] < 0.35))
    bt("S9  Triple-Edge Home",                (g["h_rest"] - g["a_rest"] >= 1) & (g["h_r_wr"] - g["a_r_wr"] > 0.15) & (g["h_r_diff"] - g["a_r_diff"] > 3))
    bt("S11 Form Gap >8",                     (g["h_r_diff"] - g["a_r_diff"]).abs() > 8, bet_home=(g["h_r_diff"] - g["a_r_diff"] > 0))
    bt("S13 Mild Form Edge",                  (g["h_r_diff"] - g["a_r_diff"] > 2.5) & (g["h_r_diff"] > 1))
    bt("S14 Home Consistency",                (g["h_r_diff"] > 5) & (g["h_r5_diff"] > 5))
    bt("S15 20g Hot Home",                    (g["h_r20_wr"] > 0.65) & (g["h_r_wr"] - g["a_r_wr"] > 0.10))
    bt("S16 Mid-Season Form + Szn Lead",      g["mid_season"] & (g["h_r_diff"] - g["a_r_diff"] > 5) & (g["h_szn_pct"] - g["a_szn_pct"] > 0.05))
    bt("S22 Mild Diff Gap 5-8",               (g["h_r_diff"] - g["a_r_diff"]).abs().between(5, 8, inclusive='right'), bet_home=(g["h_r_diff"] - g["a_r_diff"] > 0))
    bt("S23 Elite vs Elite -> Home",          (g["h_r_wr"] > 0.60) & (g["a_r_wr"] > 0.60))

    return results


def run_league(league_folder: Path, league_name: str) -> pd.DataFrame:
    csv = league_folder / "games_all.csv"
    if not csv.exists():
        print(f"  [SKIP] no games_all.csv in {league_folder.name}")
        return pd.DataFrame()
    print(f"\n>>> {league_name}")
    df = pd.read_csv(csv)
    # Only regular-season games for backtests (playoffs are short-sample)
    if "gameType" in df.columns:
        df = df[df["gameType"] == "Regular Season"]
    if len(df) < 100:
        print(f"  [SKIP] only {len(df)} regular-season games")
        return pd.DataFrame()

    g = build_features(df)
    if g.empty:
        print(f"  [SKIP] no games with enough history")
        return pd.DataFrame()
    print(f"  Backtestable games: {len(g):,}")

    results = run_strategies(g)
    if not results:
        return pd.DataFrame()
    rdf = pd.DataFrame(results).sort_values("ROI%", ascending=False)
    rdf["League"] = league_name
    print(f"  {'Strategy':<32}{'Bets':>6}{'WR%':>7}{'ROI%':>7}")
    for _, r in rdf.iterrows():
        flag = "*" if (r["WinRate%"] >= BREAKEVEN_WR and r["ROI%"] >= 2) else " "
        print(f"  {flag} {r['Strategy']:<30}{r['Bets']:>6}{r['WinRate%']:>6.1f}%{r['ROI%']:>6.1f}%")
    return rdf


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default=None, help="Filter by league name substring")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    all_results = []
    for folder in sorted(ROOT.iterdir()):
        if not folder.is_dir():
            continue
        # Skip the existing Kaggle/GitHub folders (different schema)
        if folder.name in ("Eoin A Moore Kaggle dataset", "NocturneBear GitHub",
                            "shufinskiy GitHub repo", "Wyatt Walsh Basketball"):
            continue
        league_name = folder.name.replace("_", " ")
        if args.league and args.league.lower() not in league_name.lower():
            continue
        r = run_league(folder, league_name)
        if not r.empty:
            all_results.append(r)

    if all_results:
        master = pd.concat(all_results, ignore_index=True)
        out = ROOT / "_multi_league_backtest.csv"
        master.to_csv(out, index=False)
        print(f"\n>>> Master results saved -> {out}")
        # Per-strategy: which leagues does each work in?
        print("\n>>> Strategy generalisation summary (>=52.38% WR + >=2% ROI):")
        winners = master[(master["WinRate%"] >= BREAKEVEN_WR) & (master["ROI%"] >= 2)]
        by_strat = winners.groupby("Strategy").agg(
            leagues=("League", "count"),
            avg_wr=("WinRate%", "mean"),
            avg_roi=("ROI%", "mean"),
        ).sort_values("leagues", ascending=False)
        print(by_strat.to_string())


if __name__ == "__main__":
    main()
