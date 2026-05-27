"""Base class + shared utilities for league scrapers.

Every scraper produces rows with this STRICT schema (so the backtester is
agnostic to source league):

    date          YYYY-MM-DD          local date of tipoff
    season        e.g. "2024-25"      hyphenated split-season convention
    league        e.g. "Euroleague"   identifier matching the league folder
    gameType      "Regular Season" | "Playoffs" | "Cup"
    home_team     string              normalised club name
    away_team     string              normalised club name
    home_score    int                 final score (incl OT) — 0 if not played
    away_score    int                 final score
    home_q1..q4   int|null            quarter scores when available
    away_q1..q4   int|null
    home_ot       int|null            total OT points
    away_ot       int|null

Plus optional `game_id` for the source ID (helps with deduping).

The base class enforces:
  - typed columns (so backtester won't crash on string scores)
  - chronological order
  - de-duplication on (date, home_team, away_team)
  - season inference from date if not provided
  - basic sanity checks (scores positive, dates valid, no team plays itself)
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(r"C:\Users\Ahmed Maaish\Desktop\Python\nba_data\Historic Data")

REQUIRED_COLS = [
    "date", "season", "league", "gameType",
    "home_team", "away_team",
    "home_score", "away_score",
]
OPTIONAL_COLS = [
    "home_q1", "away_q1", "home_q2", "away_q2",
    "home_q3", "away_q3", "home_q4", "away_q4",
    "home_ot", "away_ot",
    "game_id", "round", "phase",
]


def normalise_team(name: str) -> str:
    """Normalise a team name: trim, collapse whitespace, drop trailing/leading
    punctuation. Keeps unicode (e.g. Žalgiris, Beşiktaş) so it matches 22bet."""
    if not name:
        return ""
    s = unicodedata.normalize("NFC", str(name)).strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .,-_/")
    return s


def season_from_date(date_str: str) -> str:
    """Map a date YYYY-MM-DD to its split-season label (e.g. 2024-08-15 -> 2024-25).
    Basketball seasons in Europe + NBA run Sep-Jun, so games in Jul/Aug are off-season."""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    # Treat July as last possible month of previous season; Aug onwards = new season
    start_year = d.year if d.month >= 8 else d.year - 1
    return f"{start_year}-{str(start_year+1)[-2:]}"


class ScraperBase:
    league_name:   str = ""    # display name (e.g. "Euroleague")
    folder_name:   str = ""    # subfolder under Historic Data
    rate_limit_s:  float = 0.5 # sleep between HTTP requests

    def __init__(self):
        if not self.league_name or not self.folder_name:
            raise ValueError("Subclass must set league_name and folder_name")
        self.out_dir = ROOT / self.folder_name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

    # ── HTTP helper with rate-limit ─────────────────────────────────────────

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_s:
            time.sleep(self.rate_limit_s - elapsed)
        self._last_request = time.time()

    # ── To be overridden by subclasses ──────────────────────────────────────

    def fetch_season(self, season: str) -> list[dict]:
        """Return raw list of game rows for one season. Each dict must
        have at least the REQUIRED_COLS keys."""
        raise NotImplementedError

    def seasons_to_fetch(self) -> list[str]:
        """Return list of season labels to fetch."""
        # Default: last 6 split-seasons
        cur = datetime.now().year
        return [f"{cur-i}-{str(cur-i+1)[-2:]}" for i in range(6, 0, -1)]

    # ── Persistence + validation ────────────────────────────────────────────

    def save_season(self, season: str, rows: list[dict]) -> Path:
        if not rows:
            print(f"  [WARN] {self.league_name} {season}: 0 rows, skipping save")
            return None

        df = pd.DataFrame(rows)

        # Inject league + sane defaults
        df["league"] = self.league_name
        if "season" not in df.columns or df["season"].isna().all():
            df["season"] = df["date"].apply(season_from_date)
        if "gameType" not in df.columns:
            df["gameType"] = "Regular Season"

        # Type coercion
        for col in ("home_score", "away_score"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        for col in ("home_q1","away_q1","home_q2","away_q2","home_q3","away_q3",
                    "home_q4","away_q4","home_ot","away_ot"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Normalise team names
        df["home_team"] = df["home_team"].apply(normalise_team)
        df["away_team"] = df["away_team"].apply(normalise_team)

        # Reorder columns
        present = [c for c in REQUIRED_COLS + OPTIONAL_COLS if c in df.columns]
        extra = [c for c in df.columns if c not in present]
        df = df[present + extra]

        # Sort + dedupe
        df = df.sort_values("date").reset_index(drop=True)
        before = len(df)
        df = df.drop_duplicates(subset=["date","home_team","away_team"])
        dropped = before - len(df)

        out = self.out_dir / f"games_{season}.csv"
        df.to_csv(out, index=False, encoding="utf-8")
        print(f"  [OK] {self.league_name} {season}: wrote {len(df)} games (dropped {dropped} dupes) -> {out.name}")
        return out

    def consolidate(self):
        """Combine all per-season CSVs into one games_all.csv + quality report."""
        season_files = sorted(self.out_dir.glob("games_*.csv"))
        season_files = [p for p in season_files if p.name != "games_all.csv"]
        if not season_files:
            print(f"  [WARN] {self.league_name}: no season files to consolidate")
            return
        dfs = [pd.read_csv(p) for p in season_files]
        all_df = pd.concat(dfs, ignore_index=True)
        all_df = all_df.sort_values("date").drop_duplicates(
            subset=["date","home_team","away_team"]).reset_index(drop=True)
        out = self.out_dir / "games_all.csv"
        all_df.to_csv(out, index=False, encoding="utf-8")
        print(f"  [OK] Consolidated: {len(all_df)} games -> {out.name}")
        self.write_quality_report(all_df)

    def write_quality_report(self, df: pd.DataFrame):
        """Sanity check the consolidated CSV and write a human-readable report."""
        lines = [
            f"DATA QUALITY REPORT — {self.league_name}",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "=" * 60,
            f"Total games:       {len(df):,}",
            f"Date range:        {df['date'].min()} -> {df['date'].max()}",
            f"Seasons present:   {sorted(df['season'].unique().tolist())}",
            f"Unique teams:      {df['home_team'].nunique() + df['away_team'].nunique() - len(set(df['home_team']) & set(df['away_team']))}",
            "",
            "Per-season counts:",
        ]
        for season, n in df.groupby("season").size().items():
            lines.append(f"  {season}: {n:,} games")

        # Quality checks
        lines.append("")
        lines.append("Quality checks:")
        issues = 0

        # 1. Negative or zero scores in completed games
        zero_score = df[(df["home_score"] == 0) & (df["away_score"] == 0)]
        if len(zero_score) > 0:
            lines.append(f"  [WARN] {len(zero_score)} rows with 0-0 score (likely scheduled, not played)")
            issues += 1

        # 2. Suspicious scores
        big = df[(df["home_score"] > 200) | (df["away_score"] > 200)]
        if len(big) > 0:
            lines.append(f"  [WARN] {len(big)} rows with score >200 (data error?)")
            issues += 1
        tiny = df[(df["home_score"] < 30) | (df["away_score"] < 30)]
        tiny_real = tiny[(tiny["home_score"] > 0) & (tiny["away_score"] > 0)]
        if len(tiny_real) > 0:
            lines.append(f"  [WARN] {len(tiny_real)} rows with score <30 (possible scrape error)")
            issues += 1

        # 3. Team self-play
        self_play = df[df["home_team"] == df["away_team"]]
        if len(self_play) > 0:
            lines.append(f"  [WARN] {len(self_play)} rows where team plays itself (scrape error)")
            issues += 1

        # 4. Missing dates
        bad_dates = df[df["date"].isna() | (df["date"] == "")]
        if len(bad_dates) > 0:
            lines.append(f"  [WARN] {len(bad_dates)} rows with missing date")
            issues += 1

        # 5. Coverage — games per team should be roughly equal
        all_teams = pd.concat([df["home_team"], df["away_team"]])
        game_counts = all_teams.value_counts()
        if len(game_counts) > 0:
            lines.append(f"  Games per team (median): {int(game_counts.median())}")
            lines.append(f"  Min team games: {game_counts.min()} ({game_counts.idxmin()})")
            lines.append(f"  Max team games: {game_counts.max()} ({game_counts.idxmax()})")

        if issues == 0:
            lines.append("  [OK] No data quality issues detected.")
        else:
            lines.append(f"  Total issues: {issues}")

        report = "\n".join(lines)
        (self.out_dir / "_quality_report.txt").write_text(report, encoding="utf-8")
        print(report)

    # ── Main entrypoint ─────────────────────────────────────────────────────

    def run(self, seasons: list[str] = None) -> None:
        seasons = seasons or self.seasons_to_fetch()
        print(f"\n{'='*60}\n  {self.league_name} — fetching {len(seasons)} seasons\n{'='*60}")
        for s in seasons:
            try:
                rows = self.fetch_season(s)
                self.save_season(s, rows)
            except Exception as exc:
                print(f"  [ERR] {self.league_name} {s}: {exc}")
        self.consolidate()
