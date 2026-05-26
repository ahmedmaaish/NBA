"""ESPN NBA client — live scores, schedules, and team rolling stats.

Endpoints used (public, no auth):
  /scoreboard?dates=YYYYMMDD   — all games on a date
  /teams/{id}/schedule?limit=N  — recent game history for a team
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

NBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}

ROLLING_WINDOW = 10   # games for win-rate / diff stats
SHORT_WINDOW   = 5    # games for short-term momentum


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class _Cache:
    def __init__(self, ttl: float = 60.0):
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self.ttl = ttl

    def get(self, key: str):
        with self._lock:
            h = self._data.get(key)
            if h and (time.time() - h[0]) < self.ttl:
                return h[1]
        return None

    def set(self, key: str, val):
        with self._lock:
            self._data[key] = (time.time(), val)


_cache = _Cache(ttl=120.0)


def _get(url: str, params: dict = None) -> Optional[dict]:
    key = url + str(params)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    try:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            _cache.set(key, data)
            return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Score parsing — ESPN uses both plain string and {value, displayValue} dict
# ---------------------------------------------------------------------------

def _score(v) -> int:
    if isinstance(v, dict):
        return int(v.get("value") or 0)
    try:
        return int(v or 0)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Scoreboard: today + upcoming dates
# ---------------------------------------------------------------------------

def fetch_scoreboard(date: datetime = None) -> list[dict]:
    """Return list of ESPN game events for a given date (default today UTC)."""
    if date is None:
        date = datetime.now(timezone.utc)
    date_str = date.strftime("%Y%m%d")
    data = _get(f"{NBA_BASE}/scoreboard", {"dates": date_str})
    return (data or {}).get("events", [])


def fetch_schedule_window(days_ahead: int = 3) -> list[dict]:
    """Fetch all events from today through `days_ahead` days."""
    today = datetime.now(timezone.utc)
    events = []
    seen = set()
    for d in range(days_ahead + 1):
        day = today + timedelta(days=d)
        for ev in fetch_scoreboard(day):
            eid = ev.get("id")
            if eid and eid not in seen:
                seen.add(eid)
                events.append(ev)
    return events


def parse_event(ev: dict) -> Optional[dict]:
    """Extract game info from a scoreboard event."""
    comp = (ev.get("competitions") or [{}])[0]
    status = comp.get("status") or {}
    stype  = status.get("type") or {}

    state   = stype.get("state", "")    # pre / in / post
    status_name = stype.get("name", "")
    clock   = status.get("displayClock", "")
    period  = int(status.get("period") or 0)

    home = away = None
    for c in comp.get("competitors") or []:
        rec_str = next(
            (r["summary"] for r in (c.get("records") or []) if r.get("name") == "overall"),
            "0-0",
        )
        try:
            w, l = map(int, rec_str.split("-"))
        except ValueError:
            w, l = 0, 0

        obj = {
            "id":        c.get("team", {}).get("id", ""),
            "name":      c.get("team", {}).get("displayName", "?"),
            "abbr":      c.get("team", {}).get("abbreviation", "?"),
            "score":     _score(c.get("score")),
            "wins":      w,
            "losses":    l,
            "season_pct": w / max(1, w + l),
        }
        if c.get("homeAway") == "home":
            home = obj
        else:
            away = obj

    if not home or not away:
        return None

    return {
        "id":          ev.get("id", ""),
        "name":        ev.get("name", f'{away["name"]} at {home["name"]}'),
        "date_utc":    ev.get("date", ""),
        "state":       state,
        "status_name": status_name,
        "clock":       clock,
        "period":      period,
        "home":        home,
        "away":        away,
    }


# ---------------------------------------------------------------------------
# Team recent-game stats
# ---------------------------------------------------------------------------

def fetch_team_recent_games(team_id: str, limit: int = 15) -> list[dict]:
    """Return last `limit` completed games for a team with per-game stats."""
    data = _get(f"{NBA_BASE}/teams/{team_id}/schedule", {"limit": limit})
    if not data:
        return []

    results = []
    for ev in data.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        if (comp.get("status") or {}).get("type", {}).get("state") != "post":
            continue   # skip in-progress and scheduled

        date_str = (ev.get("date") or "")[:10]
        our_pts = opp_pts = None
        won = False

        for c in comp.get("competitors") or []:
            pts = _score(c.get("score"))
            if str(c.get("team", {}).get("id")) == str(team_id):
                our_pts = pts
                won = bool(c.get("winner", False))
            else:
                opp_pts = pts

        if our_pts is not None and opp_pts is not None:
            results.append({
                "date": date_str,
                "won":  won,
                "pts_for":     our_pts,
                "pts_against": opp_pts,
                "diff":        our_pts - opp_pts,
            })

    # Sort oldest to newest, return most recent `limit`
    results.sort(key=lambda x: x["date"])
    return results


def compute_team_features(games: list[dict], as_of_date: str) -> dict:
    """Compute rolling features from a list of completed game dicts.

    Args:
        games:       list of {'date', 'won', 'pts_for', 'pts_against', 'diff'}
                     sorted oldest to newest
        as_of_date:  ISO date string 'YYYY-MM-DD' (game being analysed)
    """
    # Only use games strictly before as_of_date (no lookahead)
    past = [g for g in games if g["date"] < as_of_date]

    last_n  = past[-ROLLING_WINDOW:]
    last_5  = past[-SHORT_WINDOW:]

    def _wr(g): return sum(1 for x in g if x["won"]) / max(1, len(g))
    def _diff(g): return sum(x["diff"] for x in g) / max(1, len(g))

    last_game_date = past[-1]["date"] if past else None
    try:
        days_rest = (
            datetime.strptime(as_of_date, "%Y-%m-%d") -
            datetime.strptime(last_game_date, "%Y-%m-%d")
        ).days if last_game_date else 7
    except Exception:
        days_rest = 7

    return {
        "rest":          days_rest,
        "last_game":     last_game_date,
        "r10_win_rate":  round(_wr(last_n),   3) if last_n  else None,
        "r10_diff":      round(_diff(last_n),  2) if last_n  else None,
        "r5_win_rate":   round(_wr(last_5),   3) if last_5  else None,
        "r5_diff":       round(_diff(last_5),  2) if last_5  else None,
        "games_used":    len(last_n),
    }
