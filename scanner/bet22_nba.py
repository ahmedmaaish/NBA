"""22bet NBA odds scraper.

Market IDs discovered by probing the 1xbet-family API (2026-05-26):
  G=101  T=401 / T=402  -> Moneyline  (home wins / away wins, no draw)
  G=2    T=7   / T=8    -> Point Spread (handicap, P=spread value)
  G=17   T=9   / T=10   -> Game Total  Over / Under   (P=points line)
  G=15   T=11  / T=12   -> First-Half Total Over/Under (P=half-total line)

Sport filter: SE == 'Basketball'
Live feed:   /LiveFeed/Get1x2_VZip
Pre-match:   /LineFeed/Get1x2_VZip  (available when upcoming games are listed)
"""
from __future__ import annotations

import re
import time
import threading
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

import cloudscraper

# ---------------------------------------------------------------------------
# Market IDs
# ---------------------------------------------------------------------------
G_SPREAD   = 2
G_TOTAL    = 17
G_HT_TOTAL = 15
G_WINNER   = 101

T_HOME_WIN   = 401
T_AWAY_WIN   = 402
T_SPREAD_HOME = 7   # home covers with handicap P
T_SPREAD_AWAY = 8   # away covers with handicap P
T_OVER        = 9
T_UNDER       = 10
T_HT_OVER     = 11
T_HT_UNDER    = 12

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0.0.0 Safari/537.36")

_HEADERS = {
    "User-Agent": _UA,
    "Accept":     "application/json",
    "Origin":     "https://22bet.com",
    "Referer":    "https://22bet.com/en/live",
}

LIVE_URL = ("https://22bet.com/service-api/LiveFeed/Get1x2_VZip"
            "?count=500&lng=en&mode=4&country=1&getEmpty=true")
LINE_URL = ("https://22bet.com/service-api/LineFeed/Get1x2_VZip"
            "?count=500&lng=en&mode=4&country=1&getEmpty=true")


# ---------------------------------------------------------------------------
# Team-name normalisation (same algo as football client)
# ---------------------------------------------------------------------------

_NOISE = {
    "nba", "basketball", "team", "club",
}

def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tokens(name: str) -> list[str]:
    return [t for t in _norm(name).split() if t and t not in _NOISE]

def team_similarity(a: str, b: str) -> float:
    ta = " ".join(sorted(_tokens(a)))
    tb = " ".join(sorted(_tokens(b)))
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return SequenceMatcher(None, ta, tb).ratio()


# ---------------------------------------------------------------------------
# Odds extraction
# ---------------------------------------------------------------------------

def _all_bets(event: dict):
    """Yield all bet entries from primary E[] and additional AE[].ME[]."""
    for b in event.get("E") or []:
        yield b
    for ae in event.get("AE") or []:
        for b in ae.get("ME") or []:
            yield b


def extract_winner(event: dict) -> dict:
    """Return {'home': odd, 'away': odd, 'found': bool}."""
    out = {"home": None, "away": None, "found": False}
    for b in _all_bets(event):
        if b.get("G") != G_WINNER:
            continue
        c = b.get("C")
        if c is None:
            continue
        if b.get("T") == T_HOME_WIN:
            out["home"] = float(c)
        elif b.get("T") == T_AWAY_WIN:
            out["away"] = float(c)
    out["found"] = out["home"] is not None and out["away"] is not None
    return out


def extract_spread(event: dict) -> dict:
    """Return {'line': spread, 'home_odd': X, 'away_odd': X, 'found': bool}.

    Uses the spread line closest to ±0 (the primary line).
    T=7 -> home gives points (P is positive = home favourite)
    T=8 -> away gets points (P is positive = away gets that cushion)
    We report the home-team spread (negative = favourite).
    """
    home_bets: dict[float, float] = {}  # {P: odd}
    away_bets: dict[float, float] = {}

    for b in _all_bets(event):
        if b.get("G") != G_SPREAD:
            continue
        p = b.get("P")
        c = b.get("C")
        if p is None or c is None:
            continue
        try:
            p = float(p)
            c = float(c)
        except (TypeError, ValueError):
            continue
        if b.get("T") == T_SPREAD_HOME:
            home_bets[p] = c
        elif b.get("T") == T_SPREAD_AWAY:
            away_bets[p] = c

    if not home_bets:
        return {"line": None, "home_odd": None, "away_odd": None, "found": False}

    # Pick the line closest to zero (principal line)
    best_p = min(home_bets, key=lambda x: abs(x))
    away_p = -best_p if -best_p in away_bets else None

    return {
        "line":     best_p,         # home spread (negative = home fav)
        "home_odd": home_bets[best_p],
        "away_odd": away_bets.get(away_p) or away_bets.get(best_p),
        "found":    True,
    }


def extract_total(event: dict, group: int = G_TOTAL) -> dict:
    """Return {'line': pts, 'over': odd, 'under': odd, 'found': bool}.

    Picks the line with the most balanced odds (closest to equal).
    """
    over_bets:  dict[float, float] = {}
    under_bets: dict[float, float] = {}

    t_over  = T_OVER  if group == G_TOTAL else T_HT_OVER
    t_under = T_UNDER if group == G_TOTAL else T_HT_UNDER

    for b in _all_bets(event):
        if b.get("G") != group:
            continue
        p = b.get("P")
        c = b.get("C")
        if p is None or c is None:
            continue
        try:
            p = float(p)
            c = float(c)
        except (TypeError, ValueError):
            continue
        if b.get("T") == t_over:
            over_bets[p] = c
        elif b.get("T") == t_under:
            under_bets[p] = c

    common = set(over_bets) & set(under_bets)
    if not common:
        return {"line": None, "over": None, "under": None, "found": False}

    # Pick the most balanced line (smallest |over - under| diff)
    best_p = min(common, key=lambda x: abs(over_bets[x] - under_bets[x]))
    return {
        "line":  best_p,
        "over":  over_bets[best_p],
        "under": under_bets[best_p],
        "found": True,
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Bet22NBAClient:
    """Fetch live and pre-match NBA events from 22bet with TTL caching."""

    def __init__(self, ttl: float = 30.0):
        self._scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock  = threading.Lock()
        self.ttl    = ttl

    def _fetch(self, url: str, kind: str) -> list[dict]:
        now = time.time()
        with self._lock:
            h = self._cache.get(kind)
            if h and (now - h[0]) < self.ttl:
                return h[1]
        try:
            r = self._scraper.get(url, headers=_HEADERS, timeout=20)
            if r.status_code != 200:
                return []
            events = [e for e in (r.json().get("Value") or [])
                      if e.get("SE") == "Basketball"]
            with self._lock:
                self._cache[kind] = (now, events)
            return events
        except Exception:
            return []

    def fetch_live(self) -> list[dict]:
        return self._fetch(LIVE_URL, "live")

    def fetch_prematch(self) -> list[dict]:
        return self._fetch(LINE_URL, "prematch")

    def fetch_all(self) -> list[dict]:
        live = self.fetch_live()
        pre  = self.fetch_prematch()
        seen = set()
        out  = []
        for ev in live + pre:
            eid = ev.get("I")
            if eid not in seen:
                seen.add(eid)
                out.append(ev)
        return out


# ---------------------------------------------------------------------------
# Match merging: ESPN game -> 22bet event
# ---------------------------------------------------------------------------

def best_match(espn_home: str, espn_away: str,
               events: list[dict], min_score: float = 0.50) -> Optional[dict]:
    """Find the 22bet event matching an ESPN game by fuzzy team name."""
    best_ev, best_s = None, -1.0
    for ev in events:
        o1, o2 = ev.get("O1", ""), ev.get("O2", "")
        s = max(
            (team_similarity(espn_home, o1) + team_similarity(espn_away, o2)) / 2,
            (team_similarity(espn_home, o2) + team_similarity(espn_away, o1)) / 2,
        )
        if s > best_s:
            best_s, best_ev = s, ev
    return best_ev if best_s >= min_score else None


def get_game_odds(espn_home: str, espn_away: str,
                  events: list[dict]) -> dict:
    """Return full odds dict for one game {moneyline, spread, total, ht_total}."""
    ev = best_match(espn_home, espn_away, events)
    if not ev:
        return {"found": False, "moneyline": {}, "spread": {}, "total": {}, "ht_total": {}}
    return {
        "found":    True,
        "event_id": ev.get("I"),
        "o1":       ev.get("O1"),
        "o2":       ev.get("O2"),
        "league":   ev.get("L"),
        "moneyline": extract_winner(ev),
        "spread":    extract_spread(ev),
        "total":     extract_total(ev, G_TOTAL),
        "ht_total":  extract_total(ev, G_HT_TOTAL),
    }
