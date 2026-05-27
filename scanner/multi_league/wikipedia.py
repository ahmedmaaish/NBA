"""Wikipedia season-page scraper — for national basketball leagues without
public APIs. Reads the "Results" crosstab (home along rows, away along cols).

Wikipedia structure for a season page like 2024-25 LKL:
  - Header table with team abbreviation (3 letters)
  - Results crosstab table where row = home team, column = away team (abbr),
    each cell is "85-78" (or "85–78") meaning home won/lost that game

Dates are NOT in the crosstab, so we APPROXIMATE by spreading games evenly
across the typical league season window (configurable per league).

This is "good-enough" data for form / win-rate / season-record strategies.
Rest-day strategies (S1) will be less accurate.

For each league we configure:
  - wiki_template: URL pattern with {season_start}
  - season_window: (start_month, end_month) e.g. (10, 5) for Oct-May
  - alt_names:     dict to fix common Wikipedia → 22bet name mismatches
"""
from __future__ import annotations

import re
import requests
from datetime import date, timedelta
from typing import Optional

from bs4 import BeautifulSoup

from .base import ScraperBase


DATE_DASH = re.compile(r"(\d{1,3})\s*[–\-]\s*(\d{1,3})")


def _parse_score(cell_text: str) -> Optional[tuple[int, int]]:
    """Parse a 'H–A' or 'H-A' score string. Returns None if not a valid score."""
    if not cell_text or cell_text.strip() in ("—", "–", "-", ""):
        return None
    m = DATE_DASH.search(cell_text)
    if not m:
        return None
    h, a = int(m.group(1)), int(m.group(2))
    if 30 <= h <= 200 and 30 <= a <= 200:
        return h, a
    return None


def _spread_dates(n_games: int, start_month: int, end_month: int,
                  start_year: int) -> list[str]:
    """Return n_games dates evenly spread between (start_year, start_month, 1)
    and the end of the season."""
    if start_month >= end_month:
        # Cross-year season e.g. Oct 2024 -> May 2025
        start = date(start_year, start_month, 1)
        end   = date(start_year + 1, end_month, 28)
    else:
        start = date(start_year, start_month, 1)
        end   = date(start_year, end_month, 28)
    total_days = (end - start).days
    if n_games <= 0 or total_days <= 0:
        return []
    step = total_days / n_games
    return [(start + timedelta(days=int(step * i))).isoformat()
            for i in range(n_games)]


class WikipediaScraper(ScraperBase):
    # Subclasses must set:
    wiki_template: str = ""               # e.g. "https://en.wikipedia.org/wiki/{start}–{end}_LKL_season"
    season_window: tuple[int, int] = (10, 5)
    rate_limit_s: float = 1.0
    min_score_cells: int = 50             # crosstab table must have at least N scores
    alt_names: dict[str, str] = {}        # {abbr: full_name} overrides

    def __init__(self):
        super().__init__()
        self._sess = requests.Session()
        self._sess.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 Chrome/131.0")

    def _wiki_url(self, season: str) -> str:
        start = int(season.split("-")[0])
        end_yy = season.split("-")[1]
        # Wikipedia uses en-dash in titles: 2024–25
        return self.wiki_template.format(start=start, end=end_yy)

    def _is_team_label(self, text: str) -> bool:
        """A valid team-name cell has at least 3 alpha characters and is
        NOT a score / dash / pure number."""
        if not text:
            return False
        if DATE_DASH.fullmatch(text):
            return False
        if text in ("—", "–", "-", "", "0"):
            return False
        alpha = sum(1 for c in text if c.isalpha())
        return alpha >= 3

    def fetch_season(self, season: str) -> list[dict]:
        url = self._wiki_url(season)
        self._throttle()
        r = self._sess.get(url, timeout=15)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} for {url}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")

        # Find the Results crosstab — heuristic: a wikitable with many "H-A" cells
        best_table = None
        best_score_count = 0
        for t in soup.find_all("table", class_="wikitable"):
            text = t.get_text(" ", strip=True)
            scores = DATE_DASH.findall(text)
            if len(scores) > best_score_count:
                best_score_count = len(scores)
                best_table = t

        if not best_table or best_score_count < self.min_score_cells:
            print(f"    No suitable Results table found ({best_score_count} score cells)")
            return []

        # Parse crosstab
        rows = best_table.find_all("tr")
        if len(rows) < 4:
            return []

        # First row: column headers (away teams as abbreviations or full names)
        header_cells = rows[0].find_all(["th", "td"])
        col_labels = [c.get_text(" ", strip=True) for c in header_cells[1:]]
        col_labels = [re.sub(r"\[[^\]]+\]", "", x).strip() for x in col_labels]

        # Filter to only valid team rows (first cell must be a team label)
        team_rows = []          # list of (full_name, cells_after_label)
        for r in rows[1:]:
            cells = r.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            name_text = cells[0].get_text(" ", strip=True)
            name_text = re.sub(r"\[[^\]]+\]", "", name_text).strip()
            if not self._is_team_label(name_text):
                continue
            team_rows.append((name_text, cells[1:]))

        if not team_rows:
            print(f"    No valid team rows found")
            return []

        # Build abbr -> full name map
        # If column labels are short abbreviations (≤4 chars) AND row count
        # matches column count, pair them by order
        full_names = [t[0] for t in team_rows]
        abbr_to_name: dict[str, str] = {}
        if (len(col_labels) == len(full_names)
                and all(len(c) <= 4 for c in col_labels if c)):
            for abbr, full in zip(col_labels, full_names):
                abbr_to_name[abbr] = full
        else:
            # Otherwise column labels ARE the full names
            for c in col_labels:
                if c:
                    abbr_to_name[c] = c

        abbr_to_name.update(self.alt_names)

        # Iterate cells to extract games — match column position to team index
        rows_out: list[dict] = []
        n_teams = len(team_rows)
        for r_i, (home_full, data_cells) in enumerate(team_rows):
            for c_i, cell in enumerate(data_cells):
                if c_i >= len(col_labels):
                    break
                if c_i == r_i:
                    continue   # diagonal: team vs itself
                col_label = col_labels[c_i]
                away_full = abbr_to_name.get(col_label, col_label)
                if not self._is_team_label(away_full):
                    continue
                parsed = _parse_score(cell.get_text(" ", strip=True))
                if not parsed:
                    continue
                home_score, away_score = parsed
                rows_out.append({
                    "home_team":  home_full,
                    "away_team":  away_full,
                    "home_score": home_score,
                    "away_score": away_score,
                    "season":     season,
                    "gameType":   "Regular Season",
                })

        # Approximate dates evenly across the season
        if rows_out:
            start_y = int(season.split("-")[0])
            dates = _spread_dates(len(rows_out), self.season_window[0],
                                  self.season_window[1], start_y)
            for row, d in zip(rows_out, dates):
                row["date"] = d

        print(f"    Parsed {len(rows_out)} games from {n_teams} teams (dates approximated)")
        return rows_out

    def seasons_to_fetch(self) -> list[str]:
        # Wikipedia coverage: typically 2010-11 onwards is reliable
        # We'll fetch last 8 seasons
        cur = date.today().year
        return [f"{cur-i}-{str(cur-i+1)[-2:]}" for i in range(8, 0, -1)]


# ──────────────────────────────────────────────────────────────────────────────
# League-specific subclasses
# ──────────────────────────────────────────────────────────────────────────────

class LithuaniaLKL(WikipediaScraper):
    league_name = "Lithuania LKL"
    folder_name = "Lithuania_LKL"
    # URL pattern: 2024%E2%80%9325_LKL_season (en-dash in title)
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_LKL_season"
    season_window = (10, 5)


class SpainACB(WikipediaScraper):
    league_name = "Spain ACB"
    folder_name = "Spain_ACB"
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_ACB_season"
    season_window = (9, 6)


class ItalyLBA(WikipediaScraper):
    league_name = "Italy LBA"
    folder_name = "Italy_LBA"
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_LBA_season"
    season_window = (9, 6)


class IsraelBSL(WikipediaScraper):
    league_name = "Israel BSL"
    folder_name = "Israel_BSL"
    # Israeli league names changed over time — try both 'Premier' and 'Super' League
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_Israeli_Basketball_Premier_League"
    season_window = (10, 5)


class TurkeyBSL(WikipediaScraper):
    league_name = "Turkey BSL"
    folder_name = "Turkey_BSL"
    # No _season suffix in Wikipedia page name
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_Basketbol_S%C3%BCper_Ligi"
    season_window = (10, 5)


class FranceLNB(WikipediaScraper):
    league_name = "France LNB"
    folder_name = "France_LNB"
    # France league renamed: "Pro A" -> "Élite" (with accent, URL-encoded)
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_LNB_%C3%89lite_season"
    season_window = (10, 6)


class GermanyBBL(WikipediaScraper):
    league_name = "Germany BBL"
    folder_name = "Germany_BBL"
    # No _season suffix
    wiki_template = "https://en.wikipedia.org/wiki/{start}%E2%80%93{end}_Basketball_Bundesliga"
    season_window = (10, 6)


ALL_WIKI_SCRAPERS = [
    LithuaniaLKL, SpainACB, ItalyLBA, IsraelBSL,
    TurkeyBSL,    FranceLNB, GermanyBBL,
]


if __name__ == "__main__":
    for cls in ALL_WIKI_SCRAPERS:
        cls().run()
