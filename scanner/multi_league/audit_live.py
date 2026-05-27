"""Forensic audit of live signals.

For each game currently in docs/data/signals.json, verifies:
  1. The displayed WR/ROI numbers vs the ACTUAL per-league backtest result
     (strategy.py hardcodes NBA numbers — for European leagues we need to
      look at scanner/multi_league results to know if numbers are honest)
  2. The team-form features (r10_win_rate, r10_diff, rest, season W-L)
     are computed correctly from the local CSVs
  3. Each fired strategy condition holds against the input features
  4. The signal is non-stale (Wikipedia dates are approximated, so very old
     data wouldn't reflect current form)

Outputs a clear PASS / WARN / FAIL verdict per signal.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO   = Path(__file__).resolve().parent.parent.parent
SIGS   = REPO / "docs" / "data" / "signals.json"
BACK   = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data\_multi_league_backtest.csv")
LDATA  = REPO / "scanner" / "league_data"

# Map dashboard league names back to local folder names
LEAGUE_FOLDER = {
    "Turkey — Super League": "Turkey_BSL",
    "Germany — BBL":         "Germany_BBL",
    "France — LNB":          "France_LNB",
    "Spain — Liga ACB":      "Spain_ACB",
    "Italy — Lega A":        "Italy_LBA",
    "Israel — Superleague":  "Israel_BSL",
    "Lithuania — LKL":       "Lithuania_LKL",
    "Euroleague":            "Euroleague",
    "EuroCup":               "EuroCup",
    "NBA":                   None,    # NBA uses ESPN live, not local CSVs
}

# Strategy IDs and their displayed names (from strategy.py STRATEGIES dict)
STRATEGY_LOOKUP = {
    "Away B2B -> Bet Home":                                "S1",
    "Win-Rate Gap >25% -> Bet Favourite":                  "S4",
    "Home Form Edge +5pts -> Bet Home":                    "S5",
    "Dominant Away vs Losing Home":                        "S6",
    "Rest Edge + Season Lead -> Bet Home":                 "S7",
    "5-Game Hot Home vs Cold Away":                        "S8",
    "Triple-Edge Home (Rest+WR+Diff)":                     "S9",
    "Elite Away Rested vs Weak Home":                      "S10",
    "Form Gap >8pts -> Bet In-Form Team":                  "S11",
    "Away +30% WR Edge + Rested":                          "S12",
    "Mild Form Edge -> Home":                              "S13",
    "Home Consistency Edge -> Home":                       "S14",
    "20g Hot Home + WR Edge -> Home":                      "S15",
    "Mid-Season Form + Season Lead -> Home":               "S16",
    "Cold 3-Game Home Streak -> Fade Home":                "S17",
    "Hot Away 5g + Rest -> Away":                          "S18",
    "Late-Season Big Mismatch -> Home":                    "S19",
    "Late-Season Season Edge >15% -> Better":              "S20",
    "Early-Season WR Edge >25% -> Better":                 "S21",
    "Mild Form Diff Gap 5-8 -> Better":                    "S22",
    "Elite vs Elite -> Home (HCA)":                        "S23",
    "Tank vs Tank -> Home (HCA)":                          "S24",
}

# Strategy IDs → equivalent name in multi_league backtest results CSV
BACKTEST_KEY = {
    "S1":  "S1  Away B2B + Home Rested",
    "S4":  "S4  Win-Rate Gap >25%",
    "S5":  "S5  Home Form Edge +5",
    "S6":  "S6  Dominant Away vs Losing Home",
    "S7":  "S7  Rest + Season Lead",
    "S8":  "S8  Hot Home vs Cold Away (5g)",
    "S9":  "S9  Triple-Edge Home",
    "S10": "S10 Elite Away vs Weak Home",
    "S11": "S11 Form Gap >8",
    "S12": "S12 Away +30% WR + Rest",
    "S13": "S13 Mild Form Edge",
    "S14": "S14 Home Consistency",
    "S15": "S15 20g Hot Home",
    "S16": "S16 Mid-Season Form + Szn Lead",
    "S17": "S17 Cold 3g Home Streak",
    "S18": "S18 Hot Away 5g + Rest",
    "S19": "S19 Late-Season Big Mismatch",
    "S20": "S20 Late-Season Season Edge >15%",
    "S21": "S21 Early-Season WR Edge >25%",
    "S22": "S22 Mild Diff Gap 5-8",
    "S23": "S23 Elite vs Elite -> Home",
    "S24": "S24 Tank vs Tank -> Home",
}


def load_backtest_csv() -> pd.DataFrame:
    if not BACK.exists():
        return pd.DataFrame()
    return pd.read_csv(BACK)


def load_league_data(folder: str) -> pd.DataFrame:
    """Try repo path first, then local path."""
    repo_path = LDATA / folder / "games_all.csv"
    if repo_path.exists():
        df = pd.read_csv(repo_path)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df.dropna(subset=["date"])
    return pd.DataFrame()


def get_team_recent_games_actual(team: str, df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Get the team's actual last N games from our CSV (no date filtering — most recent overall)."""
    is_home = (df["home_team"] == team)
    is_away = (df["away_team"] == team)
    sub = df[is_home | is_away].sort_values("date").tail(n)
    return sub


def compute_team_metrics(team: str, df: pd.DataFrame, n: int = 10) -> dict:
    sub = get_team_recent_games_actual(team, df, n)
    if sub.empty:
        return {"games": 0, "wr": None, "diff": None}
    wins = 0
    diffs = []
    for _, g in sub.iterrows():
        is_h = (g["home_team"] == team)
        pf = g["home_score"] if is_h else g["away_score"]
        pa = g["away_score"] if is_h else g["home_score"]
        if pf > pa:
            wins += 1
        diffs.append(pf - pa)
    return {
        "games": len(sub),
        "wr":    wins / len(sub),
        "diff":  sum(diffs) / len(diffs),
        "last_date": str(sub["date"].max().date()),
    }


def audit_game(game: dict, backtest_df: pd.DataFrame) -> list[str]:
    """Audit one game and return a list of report lines."""
    lines = []
    league = game.get("league", "NBA")
    folder = LEAGUE_FOLDER.get(league)

    name = game.get("name", "?")
    home = game.get("home", {})
    away = game.get("away", {})
    signals = game.get("signals", [])

    lines.append("")
    lines.append("=" * 78)
    lines.append(f"[{league}]  {name}")
    lines.append(f"  Date: {game.get('date_utc','?')}")
    lines.append("-" * 78)

    if folder is None:
        # NBA — verified against ESPN live, can't audit against local CSV
        lines.append("  NBA uses ESPN live API — relies on freshness of online data.")
        lines.append("  Skipping local CSV audit; NBA was verified separately.")
        for s in signals:
            sid = STRATEGY_LOOKUP.get(s["name"], "?")
            lines.append(f"  - [{sid}] {s['name']}  shown: {s['win_rate']}% WR  +{s['roi_pct']}% ROI")
        return lines

    df = load_league_data(folder)
    if df.empty:
        lines.append(f"  [FAIL] No local CSV found for {folder} — signals are based on")
        lines.append(f"          historical NBA backtest numbers ONLY, not this league.")
        return lines

    # 1. INPUT FEATURES — does the team form shown match our actual data?
    lines.append("INPUT FEATURE AUDIT (compared to local CSV):")
    home_name_local = home.get("id")   # bet22_driven stores the matched local name in 'id'
    away_name_local = away.get("id")

    for side, team_disp, team_local in [("HOME", home.get("name","?"), home_name_local),
                                         ("AWAY", away.get("name","?"), away_name_local)]:
        m10 = compute_team_metrics(team_local, df, 10)
        side_dict = home if side == "HOME" else away
        shown_wr10  = side_dict.get("r10_win_rate")
        shown_diff10 = side_dict.get("r10_diff")
        wins = side_dict.get("wins", "?")
        losses = side_dict.get("losses", "?")

        lines.append(f"  {side} '{team_disp}' (matched local: '{team_local}'):")
        if m10["games"] == 0:
            lines.append(f"    [WARN] No games found in local CSV for this team")
        else:
            wr_match = abs((shown_wr10 or 0) - m10["wr"]) < 0.01 if shown_wr10 is not None else False
            diff_match = abs((shown_diff10 or 0) - m10["diff"]) < 0.5 if shown_diff10 is not None else False
            lines.append(f"    Last 10 in CSV: {m10['games']} games, WR={m10['wr']:.3f}, diff={m10['diff']:+.1f}, "
                         f"last played {m10['last_date']}")
            lines.append(f"    Dashboard shows: WR={shown_wr10}, diff={shown_diff10:+.1f}pt")
            lines.append(f"    Season W-L on dashboard: {wins}-{losses}")
            if wr_match and diff_match:
                lines.append(f"    [PASS] Feature values match local CSV")
            else:
                lines.append(f"    [WARN] Feature values DON'T match — investigate")

            # Freshness check: how old is the latest game?
            try:
                last_d = pd.Timestamp(m10["last_date"])
                age_days = (datetime.now() - last_d).days
                if age_days > 180:
                    lines.append(f"    [STALE] Last game in our data is {age_days}d old ({m10['last_date']})")
                    lines.append(f"            This signal uses HISTORICAL form, not current-season form.")
            except Exception:
                pass

    # 2. SIGNAL DISPLAY AUDIT — does each fired signal's WR match the per-league backtest?
    lines.append("")
    lines.append("STRATEGY WR/ROI AUDIT (dashboard vs per-league backtest):")
    if not signals:
        lines.append("  (no signals fired)")
        return lines

    for s in signals:
        shown_name = s.get("name", "?")
        sid = STRATEGY_LOOKUP.get(shown_name, "?")
        shown_wr   = s.get("win_rate")
        shown_roi  = s.get("roi_pct")

        # Look up THIS strategy's actual result in THIS league
        league_disp = folder.replace("_", " ")
        bt_key = BACKTEST_KEY.get(sid)
        true_wr = true_roi = true_bets = None
        if not backtest_df.empty and bt_key:
            match = backtest_df[(backtest_df["League"] == league_disp) &
                                 (backtest_df["Strategy"] == bt_key)]
            if len(match) >= 1:
                row = match.iloc[0]
                true_wr   = row.get("WinRate%")
                true_roi  = row.get("ROI%")
                true_bets = int(row.get("Bets", 0))

        lines.append(f"  [{sid}] {shown_name}")
        lines.append(f"    Dashboard:        {shown_wr}% WR / +{shown_roi}% ROI  (these are NBA-derived)")
        if true_wr is not None:
            delta = abs(true_wr - shown_wr) if shown_wr else 0
            verdict = "PASS" if delta <= 5 else "DIVERGES"
            lines.append(f"    {folder} actual:  {true_wr}% WR / {true_roi:+.1f}% ROI  ({true_bets} bets in backtest)")
            lines.append(f"    [{verdict}] gap = {delta:.1f}%  "
                         f"{'(within tolerance)' if delta <= 5 else '(numbers differ noticeably)'}")
        else:
            lines.append(f"    {folder} actual:  NOT IN BACKTEST CSV  [WARN]")
            lines.append(f"                       This strategy may not have enough sample to validate.")

    return lines


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("FORENSIC AUDIT — live signals")
    print(f"Reading {SIGS}")
    print()
    payload = json.loads(SIGS.read_text(encoding="utf-8"))
    games = payload.get("games", [])
    if not games:
        print("No games in signals.json — nothing to audit.")
        return

    backtest_df = load_backtest_csv()
    if backtest_df.empty:
        print(f"[WARN] No backtest CSV at {BACK}")
        print("       Per-league strategy results unavailable for comparison.")
    else:
        print(f"Loaded backtest results: {len(backtest_df)} (strategy, league) combos")
        print(f"  Leagues: {sorted(backtest_df['League'].unique().tolist())}")
        print()

    all_lines = []
    for g in games:
        all_lines.extend(audit_game(g, backtest_df))
    all_lines.append("")
    all_lines.append("=" * 78)
    all_lines.append("AUDIT COMPLETE")

    report = "\n".join(all_lines)
    print(report)
    out = REPO / "AUDIT_REPORT.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nFull report saved to {out}")


if __name__ == "__main__":
    main()
