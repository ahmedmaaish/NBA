"""Euroleague + EuroCup scraper — InCrowd public JSON API.

Endpoint:
    https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/competitions/{COMP}/seasons/{COMP}{YEAR}/games?limit=500

COMP codes:
    E  -> Euroleague (top-tier European basketball, 18 teams)
    U  -> EuroCup    (second-tier European competition)

Returns full game data: scores, quarter scores, dates, phase (Regular Season,
Playoffs, Final Four), home/away, status. No auth needed, no rate-limit issues
in practice.

Each season's regular season is ~270 games (Euroleague) + playoffs.
"""
from __future__ import annotations

import requests
from datetime import datetime

from .base import ScraperBase


class _IncrowdBase(ScraperBase):
    comp_code: str = "E"
    rate_limit_s: float = 0.3

    def __init__(self):
        super().__init__()
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 Chrome/131.0")

    def fetch_season(self, season: str) -> list[dict]:
        """season is e.g. '2024-25' — extract starting year."""
        start_year = int(season.split("-")[0])
        url = (f"https://feeds.incrowdsports.com/provider/euroleague-feeds/v2/"
               f"competitions/{self.comp_code}/seasons/{self.comp_code}{start_year}/"
               f"games?limit=500")
        self._throttle()
        r = self._sess.get(url, timeout=20)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} for {url}")
            return []
        data = r.json()
        games = data.get("data") or []

        rows = []
        for g in games:
            # Skip games not yet played
            if g.get("status") != "result":
                continue

            home = g.get("home") or {}
            away = g.get("away") or {}
            date = (g.get("date") or "")[:10]  # YYYY-MM-DD
            if not date:
                continue

            phase = (g.get("phaseType") or {}).get("name") or ""
            game_type = "Regular Season"
            if "Playoff" in phase or "Final" in phase or "FF" == (g.get("phaseType") or {}).get("code"):
                game_type = "Playoffs"

            row = {
                "date":       date,
                "season":     season,
                "league":     self.league_name,
                "gameType":   game_type,
                "home_team":  home.get("name") or "",
                "away_team":  away.get("name") or "",
                "home_score": int(home.get("score") or 0),
                "away_score": int(away.get("score") or 0),
                "game_id":    g.get("identifier") or g.get("id") or "",
                "round":      ((g.get("round") or {}).get("round")),
                "phase":      phase,
            }
            # Quarter scores when present
            for side, label in [(home, "home"), (away, "away")]:
                qs = side.get("quarters") or {}
                row[f"{label}_q1"] = qs.get("q1")
                row[f"{label}_q2"] = qs.get("q2")
                row[f"{label}_q3"] = qs.get("q3")
                row[f"{label}_q4"] = qs.get("q4")
                # Sum OT periods
                ot_total = sum(v for k, v in qs.items()
                               if k.startswith("ot") and v is not None) or None
                row[f"{label}_ot"] = ot_total

            if row["home_team"] and row["away_team"]:
                rows.append(row)
        return rows

    def seasons_to_fetch(self) -> list[str]:
        # Euroleague & EuroCup data is available from ~2000-01; we'll grab the last 10 seasons
        # The dataset is small enough that 10 yrs is fine for backtesting.
        cur = datetime.now().year
        return [f"{cur-i}-{str(cur-i+1)[-2:]}" for i in range(10, 0, -1)]


class EuroleagueScraper(_IncrowdBase):
    league_name = "Euroleague"
    folder_name = "Euroleague"
    comp_code   = "E"


class EuroCupScraper(_IncrowdBase):
    league_name = "EuroCup"
    folder_name = "EuroCup"
    comp_code   = "U"


if __name__ == "__main__":
    EuroleagueScraper().run()
    EuroCupScraper().run()
