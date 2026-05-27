"""Team Wins (moneyline) vs Handicap — which actually makes more money?

Computes expected value (EV) for each strategy under both bet types using:
  - Outright WR (from backtest) — what fraction of games the team wins
  - ATS WR (from ATS backtest) — what fraction of games the team covers
  - Approximate moneyline odds based on the strategy's typical scenario
    (a strategy that picks heavy favourites = low moneyline odds)
  - Standard handicap odds (~1.90)

EV formula:
    EV per $1 bet = (WR × profit_per_win) - ((1 - WR) × stake_lost)
                  = WR × (odds - 1) - (1 - WR) × 1

Positive EV = +money long-term. Higher EV = better strategy.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data")
ATS_CSV = Path(__file__).resolve().parent.parent / "league_data" / "_ats_results.csv"


# Estimate typical moneyline odds based on the strategy's scenario.
# These are rough averages of what 22bet posts when a strategy fires.
# A heavy-favourite signal (e.g. S15) has its moneyline at ~1.30; a balanced
# signal at ~1.70; an underdog signal at ~2.40.
ESTIMATED_ML_ODDS = {
    # Heavy favourite picks (S15, S14, S16): team is expected to win big
    "S15 20g Hot Home":               1.30,
    "S14 Home Consistency":           1.35,
    "S16 Mid-Season Form + Szn Lead": 1.40,
    "S5 Home Form Edge +5":           1.45,
    # Moderate favourite picks (mid-edge)
    "S8 Hot Home vs Cold Away (5g)":  1.55,
    "S13 Mild Form Edge":             1.60,
    "S9 Triple-Edge Home":            1.50,
    "S4 Win-Rate Gap >25%":           1.50,
    "S20 Late-Season Edge >15%":      1.55,
    # Big-edge picks (one-sided)
    "S11 Form Gap >8":                1.40,
    "S6 Dominant Away vs Losing Home": 2.30,  # away team is the pick
    # Defaults
    "default": 1.70,
}

HANDICAP_ODDS = 1.90   # 22bet standard for primary handicap line


def ev_per_dollar(wr: float, odds: float) -> float:
    """Return EV per $1 staked at given win rate and decimal odds.
    Positive = +money long-term."""
    return wr * (odds - 1) - (1 - wr)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not ATS_CSV.exists():
        print(f"Missing {ATS_CSV} — run ats_check.py first")
        return

    df = pd.read_csv(ATS_CSV)
    # Average across all leagues
    agg = df.groupby("Strategy").agg(
        bets=("Bets", "sum"),
        outright_wr=("OutrightWR", "mean"),
        ats_wr=("ATS_WR", "mean"),
    ).reset_index()
    agg["outright_wr"] /= 100
    agg["ats_wr"] /= 100

    print("\n=== TEAM WINS vs HANDICAP — Expected Value Comparison ===\n")
    print("Per $10 stake, average across all backtested leagues:\n")
    print(f"{'Strategy':<34}{'ML odds':>9}{'Outright%':>11}{'ML EV/$10':>11}"
          f"{'Hcap%':>8}{'Hcap EV/$10':>13}  Winner")
    print("-" * 100)

    rows = []
    for _, r in agg.sort_values("outright_wr", ascending=False).iterrows():
        sid = r["Strategy"]
        ml_odds = ESTIMATED_ML_ODDS.get(sid, ESTIMATED_ML_ODDS["default"])
        ml_ev   = ev_per_dollar(r["outright_wr"], ml_odds) * 10
        ats_ev  = ev_per_dollar(r["ats_wr"], HANDICAP_ODDS) * 10
        winner  = "TEAM WINS"  if ml_ev > ats_ev else ("HANDICAP" if ats_ev > 0.50 else "TEAM WINS")
        # If ATS_WR is below break-even, always Team Wins
        if r["ats_wr"] < 0.524:
            winner = "TEAM WINS (handicap loses $)"
        elif ml_ev < 0 and ats_ev < 0:
            winner = "SKIP (both -EV)"
        rows.append({
            "Strategy": sid, "Bets": int(r["bets"]),
            "ML_odds": ml_odds,
            "Outright_WR": round(r["outright_wr"]*100,1),
            "ML_EV_per_10": round(ml_ev, 2),
            "ATS_WR": round(r["ats_wr"]*100,1),
            "Hcap_EV_per_10": round(ats_ev, 2),
            "Winner": winner,
        })
        print(f"{sid:<34}{ml_odds:>9.2f}{r['outright_wr']*100:>10.1f}%"
              f"{f'+${ml_ev:.2f}' if ml_ev>=0 else f'-${-ml_ev:.2f}':>11}"
              f"{r['ats_wr']*100:>7.1f}%"
              f"{(f'+${ats_ev:.2f}' if ats_ev>=0 else f'-${-ats_ev:.2f}'):>13}  {winner}")

    print("\n\nINTERPRETATION:")
    out = pd.DataFrame(rows)
    print(f"  Strategies where TEAM WINS pays more:  "
          f"{(out['Winner'].str.startswith('TEAM WINS')).sum()}")
    print(f"  Strategies where HANDICAP pays more:   "
          f"{(out['Winner'] == 'HANDICAP').sum()}")
    print(f"  Strategies that SHOULD BE SKIPPED:     "
          f"{(out['Winner'] == 'SKIP (both -EV)').sum()}")

    out.to_csv(Path(__file__).resolve().parent.parent / "league_data" / "_ev_comparison.csv",
                index=False)
    print(f"\nSaved _ev_comparison.csv")


if __name__ == "__main__":
    main()
