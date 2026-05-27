"""22bet-driven scanner — use 22bet as the source of truth for upcoming games,
and enrich each event with form/signals from our local historical CSVs.

Flow:
    1. Pull every basketball event from 22bet
    2. For each event, identify the league (via 22bet's L field)
    3. If we have backtest data for that league, load each team's history
       from nba_data/Historic Data/<folder>/games_all.csv
    4. Compute the same rolling features as the live scanner
    5. Run the strategy library
    6. Return game dicts shaped like espn_nba.parse_event()

This gives signals for ANY league 22bet covers, as long as we have historical
data for it — not just NBA + Euroleague.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import pandas as pd

# Map 22bet league labels -> our local folder names
LEAGUE_MAP = {
    "Spain. Liga ACB":          "Spain_ACB",
    "Spain. Liga Endesa":       "Spain_ACB",
    "Germany. BBL":             "Germany_BBL",
    "Germany. Basketball Bundesliga": "Germany_BBL",
    "Turkey. Super League":     "Turkey_BSL",
    "Turkey. BSL":              "Turkey_BSL",
    "Turkey. EBBL":             None,  # 2nd-tier, not in our backtest data
    "France. LNB":              "France_LNB",
    "France. LNB Elite":        "France_LNB",
    "Italy. Lega A":            "Italy_LBA",
    "Italy. LBA":               "Italy_LBA",
    "Israel. Superleague":      "Israel_BSL",
    "Israel. BSL":              "Israel_BSL",
    "Israel. National League":  None,  # 2nd-tier
    "Israel. Liga Alef":        None,  # 3rd-tier
    "Israel. Youth League":     None,  # youth, skip
    "Lithuania. LKL":           "Lithuania_LKL",
    "Estonia. Korvpalli Meistriliiga": None,  # no data
    "Georgia. Superliga":       None,
    "Romania. Liga Nationala":  None,
    "Romania. Liga 1":          None,
    "China. CDBL":              None,
    "China. CBA":               None,  # could add later
    "Mexico. CIBACOPA":         None,
}

# Path lookup with fallback so it works both locally AND in GitHub Actions.
# Priority:
#   1. Local Windows path (developer machine, has full historical data)
#   2. Repo-bundled path (committed under scanner/league_data/)
# If neither exists, 22bet-driven discovery silently returns no extra games
# instead of crashing the whole pipeline.
_LOCAL_PATH = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data")
_REPO_PATH  = Path(__file__).resolve().parent / "league_data"

if _LOCAL_PATH.exists():
    HISTORIC_ROOT = _LOCAL_PATH
elif _REPO_PATH.exists():
    HISTORIC_ROOT = _REPO_PATH
else:
    HISTORIC_ROOT = _REPO_PATH   # empty path — load functions will return empty


def _norm(s: str) -> str:
    """Normalise a team name for fuzzy matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _team_sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


@lru_cache(maxsize=None)
def _load_league_history(folder: str) -> pd.DataFrame:
    """Cache load of one league's games_all.csv."""
    f = HISTORIC_ROOT / folder / "games_all.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    return df.sort_values("date").reset_index(drop=True)


def _all_team_names(df: pd.DataFrame) -> set[str]:
    return set(df["home_team"].dropna().unique()) | set(df["away_team"].dropna().unique())


def find_team_in_history(bet_name: str, df: pd.DataFrame) -> str | None:
    """Find the best-matching team name in the historical data."""
    if df.empty:
        return None
    teams = _all_team_names(df)
    best = None
    best_score = 0.0
    target = _norm(bet_name)
    for t in teams:
        s = _team_sim(bet_name, t)
        if s > best_score:
            best_score = s
            best = t
    if best_score >= 0.55:
        return best
    return None


def get_team_recent_games(team_name: str, df: pd.DataFrame,
                          before_date: str, limit: int = 30) -> list[dict]:
    """Return last N games for a team before a given date, as the espn_nba
    format: [{'date','won','pts_for','pts_against','diff'}, ...] oldest first."""
    if df.empty:
        return []
    before = pd.Timestamp(before_date)
    is_home = (df["home_team"] == team_name) & (df["date"] < before)
    is_away = (df["away_team"] == team_name) & (df["date"] < before)
    sub = df[is_home | is_away].tail(limit).copy()
    rows = []
    for _, g in sub.iterrows():
        team_at_home = (g["home_team"] == team_name)
        pf = int(g["home_score"]) if team_at_home else int(g["away_score"])
        pa = int(g["away_score"]) if team_at_home else int(g["home_score"])
        rows.append({
            "date":        g["date"].date().isoformat(),
            "won":         pf > pa,
            "pts_for":     pf,
            "pts_against": pa,
            "diff":        pf - pa,
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def _abbreviate(name: str) -> str:
    if not name:
        return "?"
    parts = name.split()
    if len(parts) == 1:
        return parts[0][:3].upper()
    return "".join(p[0].upper() for p in parts if p[0].isalpha())[:4]


def _season_record(team_name: str, df: pd.DataFrame, before_date: str) -> tuple[int, int]:
    """Compute team's current-season W-L going into a given date."""
    before = pd.Timestamp(before_date)
    season_start = before - timedelta(days=270)  # rough season cutoff
    is_home = (df["home_team"] == team_name) & (df["date"] < before) & (df["date"] >= season_start)
    is_away = (df["away_team"] == team_name) & (df["date"] < before) & (df["date"] >= season_start)
    sub = df[is_home | is_away]
    wins = losses = 0
    for _, g in sub.iterrows():
        is_h = (g["home_team"] == team_name)
        pf = g["home_score"] if is_h else g["away_score"]
        pa = g["away_score"] if is_h else g["home_score"]
        if pf > pa: wins += 1
        else:       losses += 1
    return wins, losses


def event_to_game(event: dict) -> dict | None:
    """Convert a 22bet basketball event into our internal game shape.
    Returns None if the league isn't in our backtest data."""
    league_label = event.get("L", "")
    folder = LEAGUE_MAP.get(league_label)
    if not folder:
        return None

    df = _load_league_history(folder)
    if df.empty:
        return None

    o1 = event.get("O1", "")   # 22bet's "team 1" (typically home or first listed)
    o2 = event.get("O2", "")
    if not o1 or not o2:
        return None

    # 22bet doesn't always mark which side is home for international games.
    # For our strategies, we'll treat O1 as home (matches the same convention
    # we use in bet22_nba.py: O1 paired with T_HOME_WIN=401).
    home_match = find_team_in_history(o1, df)
    away_match = find_team_in_history(o2, df)
    if not home_match or not away_match:
        return None

    # Game date from 22bet's S field (unix timestamp)
    ts = event.get("S")
    try:
        d_utc = datetime.fromtimestamp(int(ts), timezone.utc) if ts else None
    except Exception:
        d_utc = None
    if not d_utc:
        return None
    date_str = d_utc.date().isoformat()

    # Build season records (using our historical data as a proxy)
    h_w, h_l = _season_record(home_match, df, date_str)
    a_w, a_l = _season_record(away_match, df, date_str)

    league_display = league_label.replace(".", " —").strip()
    return {
        "id":          str(event.get("I", "")),
        "name":        f"{o2} at {o1}",
        "date_utc":    d_utc.isoformat().replace("+00:00", "Z"),
        "state":       "pre",
        "status_name": "STATUS_SCHEDULED",
        "clock":       "0.0",
        "period":      0,
        "league":      league_display,
        "_history_fn": "bet22_driven",
        "_folder":     folder,
        "home": {
            "id":         home_match,
            "name":       o1,
            "abbr":       _abbreviate(o1),
            "score":      0,
            "wins":       h_w,
            "losses":     h_l,
            "season_pct": h_w / max(1, h_w + h_l),
        },
        "away": {
            "id":         away_match,
            "name":       o2,
            "abbr":       _abbreviate(o2),
            "score":      0,
            "wins":       a_w,
            "losses":     a_l,
            "season_pct": a_w / max(1, a_w + a_l),
        },
    }


def fetch_team_recent_games_local(team_match: str, folder: str, limit: int = 30,
                                   before_date: str = None) -> list[dict]:
    """Public entrypoint for update.py — looks up team form from our CSVs."""
    df = _load_league_history(folder)
    if df.empty:
        return []
    if before_date is None:
        before_date = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    return get_team_recent_games(team_match, df, before_date, limit)
