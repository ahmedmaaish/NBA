"""Multi-league basketball scrapers — collect historical game data for
backtesting against the existing strategy library.

All scrapers write a consistent schema to:
    nba_data/Historic Data/<LeagueFolder>/games_<season>.csv
    nba_data/Historic Data/<LeagueFolder>/games_all.csv     (consolidated)
    nba_data/Historic Data/<LeagueFolder>/_quality_report.txt
"""
