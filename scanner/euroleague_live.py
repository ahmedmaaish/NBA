"""Live Euroleague + EuroCup data client — uses the same InCrowd API that
powers the historical scraper, but pulls upcoming/in-progress games.

Returns games in the SAME shape as espn_nba.parse_event() so the rest of the
pipeline (strategies, 22bet matching, JSON writer) treats them uniformly.

Each game dict includes:
    id, name, date_utc, state ('pre'|'in'|'post'),
    status_name, clock, period,
    home: { id, name, abbr, score, wins, losses, season_pct },
    away: { ... }
    league: 'Euroleague' or 'EuroCup'
"""
from __future__ import annotations

import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

NBA_USERAGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/131.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": NBA_USERAGENT, "Accept": "application/json"}

# Competition codes
EUROLEAGUE_CODE = "E"
EUROCUP_CODE    = "U"


def _build_url(comp_code: str, season_year: int) -> str:
    return (f"https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/"
            f"competitions/{comp_code}/seasons/{comp_code}{season_year}/"
            f"games?limit=500")


def _current_season_year() -> int:
    """Euroleague season starts in October. Aug 1 = new season."""
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def _abbreviate(name: str) -> str:
    """Make a 3-4 letter abbreviation from a team name."""
    if not name:
        return "?"
    parts = name.replace("-", " ").split()
    if len(parts) == 1:
        return parts[0][:3].upper()
    return "".join(p[0].upper() for p in parts if p[0].isalpha())[:4]


def _parse_game(g: dict, league: str) -> Optional[dict]:
    """Convert an InCrowd game dict to our internal game shape."""
    home_raw = g.get("home") or {}
    away_raw = g.get("away") or {}
    if not home_raw.get("name") or not away_raw.get("name"):
        return None

    date_str = g.get("date") or ""   # e.g. "2026-05-27T17:00:00.000Z"
    status   = g.get("status")        # "scheduled" | "live" | "result"
    state_map = {
        "scheduled": "pre",
        "live":      "in",
        "result":    "post",
        "finished":  "post",
    }
    state = state_map.get(status, "pre")

    # InCrowd doesn't expose pre-game W-L records directly; the team
    # standings would need a separate fetch. For now we'll default to 0-0
    # and the strategies will skip if games_used<3 anyway.
    def build_side(raw):
        score = raw.get("score") or 0
        return {
            "id":        raw.get("code") or raw.get("name", "?"),
            "name":      raw.get("name", "?"),
            "abbr":      raw.get("tla") or _abbreviate(raw.get("name", "?")),
            "score":     int(score),
            "wins":      0,           # populated from a separate /standings call
            "losses":    0,
            "season_pct": 0.5,
        }

    home_obj = build_side(home_raw)
    away_obj = build_side(away_raw)

    name = f"{away_obj['name']} at {home_obj['name']}"

    return {
        "id":          str(g.get("identifier") or g.get("id", "")),
        "name":        name,
        "date_utc":    date_str,
        "state":       state,
        "status_name": f"STATUS_{status.upper() if status else 'SCHEDULED'}",
        "clock":       g.get("remainingTime") or "0.0",
        "period":      g.get("quarter") or 0,
        "home":        home_obj,
        "away":        away_obj,
        "league":      league,
    }


def _fetch_standings(comp_code: str, season_year: int) -> dict[str, tuple[int,int]]:
    """Get team standings (wins/losses) for a given season.

    Returns {team_code: (wins, losses)}.  If standings unavailable, returns {}.
    """
    url = (f"https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/"
           f"competitions/{comp_code}/seasons/{comp_code}{season_year}/standings")
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
        teams = data.get("data") or []
        out = {}
        for t in teams:
            code = t.get("club", {}).get("code") or t.get("code")
            wins = int(t.get("gamesWon") or 0)
            losses = int(t.get("gamesLost") or 0)
            if code:
                out[code] = (wins, losses)
        return out
    except Exception:
        return {}


def fetch_upcoming(comp_code: str = EUROLEAGUE_CODE,
                   league_label: str = "Euroleague",
                   days_ahead: int = 7) -> list[dict]:
    """Return all Euroleague (or EuroCup) games in the next `days_ahead` days
    plus today's in-progress and recently-completed games."""
    year = _current_season_year()
    url  = _build_url(comp_code, year)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    raw = data.get("data") or []
    now = datetime.now(timezone.utc)
    cutoff_past   = now - timedelta(hours=12)
    cutoff_future = now + timedelta(days=days_ahead)

    standings = _fetch_standings(comp_code, year)

    out = []
    for g in raw:
        date_str = g.get("date")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if d < cutoff_past or d > cutoff_future:
            continue
        parsed = _parse_game(g, league_label)
        if not parsed:
            continue
        # Hydrate standings
        for side_key in ("home", "away"):
            tid = parsed[side_key]["id"]
            if tid in standings:
                w, l = standings[tid]
                parsed[side_key]["wins"]   = w
                parsed[side_key]["losses"] = l
                parsed[side_key]["season_pct"] = w / max(1, w + l)
        out.append(parsed)
    return out


def fetch_team_recent_games(team_code: str, comp_code: str = EUROLEAGUE_CODE,
                             limit: int = 30) -> list[dict]:
    """Return last `limit` completed games for a Euroleague team.

    Output matches espn_nba.fetch_team_recent_games shape:
        [{ 'date', 'won', 'pts_for', 'pts_against', 'diff' }, ...]
        sorted oldest -> newest
    """
    year = _current_season_year()
    url  = _build_url(comp_code, year)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    raw = data.get("data") or []
    rows: list[dict] = []
    for g in raw:
        if g.get("status") != "result":
            continue
        home = g.get("home") or {}
        away = g.get("away") or {}
        date_str = (g.get("date") or "")[:10]
        if not date_str:
            continue
        hc = home.get("code"); ac = away.get("code")
        hs = int(home.get("score") or 0); as_ = int(away.get("score") or 0)
        if hc == team_code:
            rows.append({
                "date": date_str,
                "won":  hs > as_,
                "pts_for": hs,
                "pts_against": as_,
                "diff": hs - as_,
            })
        elif ac == team_code:
            rows.append({
                "date": date_str,
                "won":  as_ > hs,
                "pts_for": as_,
                "pts_against": hs,
                "diff": as_ - hs,
            })
    rows.sort(key=lambda x: x["date"])
    return rows[-limit:]
