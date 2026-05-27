"""Master runner — scrapes every league for every available season.

Usage:
    python -m scanner.multi_league.run_all                # all leagues, default season range
    python -m scanner.multi_league.run_all --euroleague   # just one league
    python -m scanner.multi_league.run_all --seasons 5    # last 5 seasons only

After running, see:
    nba_data/Historic Data/_INVENTORY.txt           — master summary
    nba_data/Historic Data/<league>/games_all.csv   — consolidated per-league
    nba_data/Historic Data/<league>/_quality_report.txt
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from .base import ROOT
from .euroleague import EuroleagueScraper, EuroCupScraper
from .wikipedia import (LithuaniaLKL, SpainACB, ItalyLBA, IsraelBSL,
                        TurkeyBSL, FranceLNB, GermanyBBL)


# All scrapers ordered by data-quality tier:
#   API     — exact dates, official source
#   wiki    — approximate dates, scraped from Wikipedia (~5-10% noise)
ALL_SCRAPERS = [
    # (Class, tier)
    (EuroleagueScraper, "API"),
    (EuroCupScraper,    "API"),
    (LithuaniaLKL,      "wiki"),
    (SpainACB,          "wiki"),
    (ItalyLBA,          "wiki"),
    (IsraelBSL,         "wiki"),
    (TurkeyBSL,         "wiki"),
    (FranceLNB,         "wiki"),
    (GermanyBBL,        "wiki"),
]


def seasons_window(years: int) -> list[str]:
    """Return last `years` split-season labels (newest last)."""
    cur = datetime.now().year
    return [f"{cur-i}-{str(cur-i+1)[-2:]}" for i in range(years, 0, -1)]


def write_master_inventory():
    """Walk every league folder and write a top-level summary of what we have."""
    lines = [
        "MASTER INVENTORY — Historic Basketball Data",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "=" * 70,
        f"Root: {ROOT}",
        "",
        f"{'League':<25} {'Tier':<6} {'Games':>8} {'Seasons':<14} {'Teams':>6}",
        "-" * 70,
    ]

    grand_total = 0
    for cls, tier in ALL_SCRAPERS:
        s = cls()
        all_csv = s.out_dir / "games_all.csv"
        if not all_csv.exists():
            lines.append(f"{s.league_name:<25} {tier:<6} {'--':>8} {'(no data)':<14} {'--':>6}")
            continue
        df = pd.read_csv(all_csv)
        seasons = sorted(df["season"].unique().tolist())
        n_seasons = len(seasons)
        season_range = f"{seasons[0]} … {seasons[-1]}" if len(seasons) > 1 else seasons[0]
        teams = pd.concat([df["home_team"], df["away_team"]]).nunique()
        lines.append(f"{s.league_name:<25} {tier:<6} {len(df):>8,}  {season_range:<14} {teams:>6}")
        grand_total += len(df)

    # NBA from Eoin Moore Kaggle dataset (existing, not scraped)
    nba_path = ROOT / "Eoin A Moore Kaggle dataset" / "TeamStatistics.csv"
    if nba_path.exists():
        try:
            nba_df = pd.read_csv(nba_path, parse_dates=["gameDateTimeEst"], low_memory=False)
            n_games = nba_df["gameId"].nunique()
            grand_total += n_games
            yr_min = nba_df["gameDateTimeEst"].min().year
            yr_max = nba_df["gameDateTimeEst"].max().year
            lines.append(f"{'NBA':<25} {'API':<6} {n_games:>8,}  {f'{yr_min} … {yr_max}':<14} {'30':>6}")
        except Exception as e:
            lines.append(f"{'NBA':<25} {'API':<6} {'?':>8} {'(parse err)':<14} {'?':>6}")

    lines.append("-" * 70)
    lines.append(f"{'TOTAL':<32} {grand_total:>8,} games across all leagues")
    lines.append("")
    lines.append("Notes:")
    lines.append("  - API tier: exact game dates, full quarter scores when available")
    lines.append("  - wiki tier: dates approximated (spread evenly across season window)")
    lines.append("  - Use Euroleague + EuroCup + NBA for highest-quality backtests")
    lines.append("  - National leagues good for form/win-rate signals; rest-day strategies")
    lines.append("    will be less accurate due to approximate dates")

    out = ROOT / "_INVENTORY.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nWritten: {out}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", type=int, default=5,
                   help="How many seasons back to fetch (default 5)")
    p.add_argument("--only", default=None,
                   help="Run only one league by class name (e.g. EuroleagueScraper)")
    p.add_argument("--inventory-only", action="store_true",
                   help="Just rebuild the inventory file, no scraping")
    args = p.parse_args()

    if args.inventory_only:
        write_master_inventory()
        return

    seasons = seasons_window(args.seasons)
    scrapers = ALL_SCRAPERS
    if args.only:
        scrapers = [(c, t) for c, t in ALL_SCRAPERS if c.__name__ == args.only]
        if not scrapers:
            print(f"No scraper class named {args.only}")
            print(f"Available: {[c.__name__ for c, _ in ALL_SCRAPERS]}")
            sys.exit(1)

    print(f"\n>>> RUNNING {len(scrapers)} SCRAPERS x {len(seasons)} SEASONS = {len(scrapers)*len(seasons)} fetches\n")
    for cls, tier in scrapers:
        try:
            cls().run(seasons=seasons)
        except Exception as e:
            print(f"  [ERR] {cls.__name__}: {e}")

    write_master_inventory()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
