"""Generate historical example games for the dashboard 'Examples' section.

Picks ~12 real regular-season games (2023-2025) where the strongest strategies
fired, plus the actual outcome. Lets users see how the app would have looked
during the regular season — a teaching tool during the off-season.

Run once locally:
    python -m scanner.demo_examples
Writes: docs/data/examples.json
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

DATA_DIR = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data\Eoin A Moore Kaggle dataset")
OUT      = Path(__file__).resolve().parent.parent / "docs" / "data" / "examples.json"

N_GAMES = 10
SAMPLE_PER_STRATEGY = 2   # 2 real winning examples per top strategy

# We pick a few showcase strategies that fire often + have realistic edges
SHOWCASE = ["S1", "S9", "S13", "S14", "S15", "S16", "S22", "S23"]


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw[raw["gameType"] == "Regular Season"].copy()
    df = df.sort_values(["teamId", "gameDateTimeEst"]).reset_index(drop=True)

    df["pt_diff"] = df["teamScore"] - df["opponentScore"]
    df["win_int"] = df["win"].astype(int)
    grp = df.groupby("teamId")

    def roll(col, w, min_p=None):
        if min_p is None:
            min_p = max(3, w // 3)
        return grp[col].transform(lambda x: x.shift(1).rolling(w, min_periods=min_p).mean())

    df["r_diff"]  = roll("pt_diff", N_GAMES)
    df["r_wr"]    = roll("win_int", N_GAMES)
    df["r5_wr"]   = roll("win_int", 5)
    df["r5_diff"] = roll("pt_diff", 5)
    df["r20_wr"]  = roll("win_int", 20, 5)

    df["prev_date"] = grp["gameDateTimeEst"].transform(lambda x: x.shift(1))
    df["rest"] = (df["gameDateTimeEst"] - df["prev_date"]).dt.days.fillna(7).clip(0, 14).astype(int)

    df["szn_wins_pre"]   = grp["seasonWins"].transform(lambda x: x.shift(1)).fillna(0)
    df["szn_losses_pre"] = grp["seasonLosses"].transform(lambda x: x.shift(1)).fillna(0)
    df["szn_games"]      = df["szn_wins_pre"] + df["szn_losses_pre"]
    df["szn_pct"]        = df["szn_wins_pre"] / (df["szn_games"] + 1e-6)
    return df


def to_game_df(ts: pd.DataFrame) -> pd.DataFrame:
    keep = ["gameId","gameDateTimeEst","teamId","teamName","win_int",
            "teamScore","opponentScore",
            "rest","r_wr","r5_wr","r5_diff","r_diff","r20_wr","szn_pct"]
    h = ts[ts["home"]][keep].rename(columns={
        "teamId":"h_team","teamName":"h_name","win_int":"home_win",
        "teamScore":"h_score","opponentScore":"a_score",
        "rest":"h_rest","r_wr":"h_wr","r5_wr":"h_wr5","r5_diff":"h_diff5",
        "r_diff":"h_diff","r20_wr":"h_wr20","szn_pct":"h_szn",
    })
    a = ts[~ts["home"]][keep].rename(columns={
        "teamId":"a_team","teamName":"a_name","win_int":"away_win",
        "teamScore":"a_score_x","opponentScore":"h_score_x",
        "rest":"a_rest","r_wr":"a_wr","r5_wr":"a_wr5","r5_diff":"a_diff5",
        "r_diff":"a_diff","r20_wr":"a_wr20","szn_pct":"a_szn",
    }).drop(columns=["gameDateTimeEst"])
    g = h.merge(a, on="gameId", how="inner")
    g = g.dropna(subset=["h_wr","a_wr","h_diff","a_diff"])
    g["date"] = pd.to_datetime(g["gameDateTimeEst"])
    g["season_month"] = ((g["date"].dt.month - 10) % 12) + 1
    g["mid_season"]   = g["season_month"].between(3, 6)
    return g.sort_values("date").reset_index(drop=True)


def fired(g, sid):
    """Returns boolean Series: which rows the strategy fires on."""
    if sid == "S1":  return (g["a_rest"] == 1) & (g["h_rest"] >= 2)
    if sid == "S9":  return (g["h_rest"] - g["a_rest"] >= 1) & ((g["h_wr"] - g["a_wr"]) > 0.15) & ((g["h_diff"] - g["a_diff"]) > 3)
    if sid == "S13": return ((g["h_diff"] - g["a_diff"]) > 2.5) & (g["h_diff"] > 1)
    if sid == "S14": return (g["h_diff"] > 5) & (g["h_diff5"] > 5)
    if sid == "S15": return (g["h_wr20"] > 0.65) & ((g["h_wr"] - g["a_wr"]) > 0.10)
    if sid == "S16": return (g["mid_season"]) & ((g["h_diff"] - g["a_diff"]) > 5) & ((g["h_szn"] - g["a_szn"]) > 0.05)
    if sid == "S22": diff_gap = g["h_diff"] - g["a_diff"]; return (diff_gap.abs() > 5) & (diff_gap.abs() <= 8)
    if sid == "S23": return (g["h_wr"] > 0.60) & (g["a_wr"] > 0.60)
    return pd.Series(False, index=g.index)


def bet_side(sid, g_row):
    """Which side does each strategy bet?"""
    if sid in ("S1","S9","S13","S14","S15","S16","S23"): return "home"
    if sid == "S22":
        return "home" if (g_row["h_diff"] - g_row["a_diff"]) > 0 else "away"
    return "home"


STRATEGY_NAMES = {
    "S1":  "Away B2B -> Bet Home",
    "S9":  "Triple-Edge Home",
    "S13": "Mild Form Edge -> Home",
    "S14": "Home Consistency Edge",
    "S15": "20g Hot Home + WR Edge",
    "S16": "Mid-Season Form + Season Lead",
    "S22": "Mild Diff Gap 5-8",
    "S23": "Elite vs Elite -> Home",
}


def main():
    print(f"Loading TeamStatistics.csv...")
    raw = pd.read_csv(DATA_DIR / "TeamStatistics.csv",
                      parse_dates=["gameDateTimeEst"], low_memory=False)
    for col in ("home", "win"):
        if raw[col].dtype == object:
            raw[col] = raw[col].map({"True":True,"False":False,"1":True,"0":False}).fillna(raw[col])
        raw[col] = raw[col].astype(bool)
    ts = build_features(raw)
    g = to_game_df(ts)

    # Pick games from the most recent regular season (2024-25)
    recent_mask = (g["date"] >= "2024-10-01") & (g["date"] <= "2025-04-30")
    rg = g[recent_mask].copy()
    print(f"Recent regular season games: {len(rg):,}")

    examples = []
    for sid in SHOWCASE:
        mask = fired(rg, sid)
        df = rg[mask].copy()
        if df.empty: continue
        # Take 2 winning bets (pick recent ones)
        wins = []
        for _, row in df.tail(50).iterrows():   # last 50 firings of this strategy
            side = bet_side(sid, row)
            won = (row["home_win"] == 1) if side == "home" else (row["home_win"] == 0)
            if won:
                wins.append(row)
            if len(wins) >= SAMPLE_PER_STRATEGY:
                break
        for row in wins:
            side = bet_side(sid, row)
            examples.append({
                "strategy_id":   sid,
                "strategy_name": STRATEGY_NAMES[sid],
                "date":          str(row["date"].date()),
                "matchup":       f"{row['a_name']} @ {row['h_name']}",
                "h_team":        row["h_name"], "a_team": row["a_name"],
                "h_score":       int(row["h_score"]), "a_score": int(row["a_score"]),
                "h_wr":          round(float(row["h_wr"]), 2),
                "a_wr":          round(float(row["a_wr"]), 2),
                "h_diff":        round(float(row["h_diff"]), 1),
                "a_diff":        round(float(row["a_diff"]), 1),
                "h_rest":        int(row["h_rest"]),
                "a_rest":        int(row["a_rest"]),
                "bet_side":      side,
                "result":        "WON" if ((row["home_win"]==1 and side=="home") or
                                            (row["home_win"]==0 and side=="away")) else "LOST",
                "final_score":   f"{int(row['h_score'])}-{int(row['a_score'])}",
            })

    # Sort newest first
    examples.sort(key=lambda x: x["date"], reverse=True)
    out = {
        "generated_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "season":        "2024-25 Regular Season",
        "examples":      examples,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(examples)} example games to {OUT}")
    for e in examples[:5]:
        print(f"  {e['date']}  {e['matchup']}  -> {e['strategy_id']} bet {e['bet_side']} -> {e['result']} ({e['final_score']})")


if __name__ == "__main__":
    main()
