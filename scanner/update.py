"""GitHub Actions runner — fetches ESPN NBA data + 22bet odds, writes signals.json.

Run:  python -m scanner.update
Output: docs/data/signals.json
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .espn_nba         import fetch_schedule_window, parse_event, fetch_team_recent_games, compute_team_features
from .bet22_nba        import Bet22NBAClient, get_game_odds
from .strategy         import evaluate_game, rank_signals, top_signal
from .euroleague_live  import (fetch_upcoming  as fetch_euroleague_upcoming,
                               fetch_team_recent_games as fetch_el_team_history,
                               EUROLEAGUE_CODE, EUROCUP_CODE)
from .bet22_driven     import event_to_game as bet22_event_to_game, fetch_team_recent_games_local

OUT_FILE = Path(__file__).resolve().parent.parent / "docs" / "data" / "signals.json"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def build_signals() -> dict:
    now_utc = datetime.now(timezone.utc)

    # ── 1a. Fetch ESPN NBA schedule (today + next 3 days) ─────────────────
    _log("Fetching ESPN NBA schedule...")
    raw_events = fetch_schedule_window(days_ahead=3)
    _log(f"  {len(raw_events)} NBA raw events")

    games_raw = []
    for ev in raw_events:
        parsed = parse_event(ev)
        if parsed:
            parsed["league"] = "NBA"
            parsed["_history_fn"] = "espn"
            games_raw.append(parsed)
    _log(f"  {len(games_raw)} parseable NBA games")

    # ── 1b. Fetch Euroleague + EuroCup upcoming games ─────────────────────
    for comp_code, label in [(EUROLEAGUE_CODE, "Euroleague"), (EUROCUP_CODE, "EuroCup")]:
        _log(f"Fetching {label} schedule...")
        try:
            el_games = fetch_euroleague_upcoming(comp_code, label, days_ahead=7)
            for g in el_games:
                g["_history_fn"] = comp_code
            games_raw.extend(el_games)
            _log(f"  {len(el_games)} {label} games added")
        except Exception as exc:
            _log(f"  {label} fetch failed: {exc}")

    # ── 1c. Discover games from 22bet for any league we have data for ─────
    _log("Fetching 22bet basketball events for league-driven discovery...")
    bet22_client_early = Bet22NBAClient(ttl=30.0)
    try:
        bet22_events_early = bet22_client_early.fetch_all()
        added = 0
        # Avoid double-counting events we already got from NBA/Euroleague
        existing_ids = {g["id"] for g in games_raw}
        for ev in bet22_events_early:
            game = bet22_event_to_game(ev)
            if game and game["id"] not in existing_ids:
                games_raw.append(game)
                added += 1
        _log(f"  {added} additional games discovered via 22bet (Wikipedia-backed leagues)")
    except Exception as exc:
        _log(f"  22bet-driven discovery failed: {exc}")
        bet22_events_early = []

    if not games_raw:
        _log("No games found — writing empty signals.json")
        return {
            "updated_utc": now_utc.isoformat(timespec="seconds"),
            "games": [],
            "meta": {"msg": "No NBA games scheduled in next 3 days."},
        }

    # ── 2. Collect unique (team_id, history_fn, folder) so we fetch from the right source ─
    team_jobs: set[tuple[str, str, str]] = set()
    for g in games_raw:
        hf = g.get("_history_fn", "espn")
        folder = g.get("_folder", "")
        team_jobs.add((g["home"]["id"], hf, folder))
        team_jobs.add((g["away"]["id"], hf, folder))
    _log(f"Fetching recent game history for {len(team_jobs)} teams (across leagues)...")

    def fetch_one(tid: str, hf: str, folder: str = None) -> list[dict]:
        if hf == "espn":
            return fetch_team_recent_games(tid, 30)
        if hf == "bet22_driven" and folder:
            return fetch_team_recent_games_local(tid, folder, 30)
        # Euroleague or EuroCup code
        return fetch_el_team_history(tid, hf, 30)

    # Parallel fetch — keyed by (team_id, source, folder)
    team_history: dict[tuple[str, str, str], list[dict]] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        future_map = {pool.submit(fetch_one, tid, hf, folder): (tid, hf, folder)
                      for tid, hf, folder in team_jobs}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                team_history[key] = fut.result()
            except Exception as exc:
                _log(f"  WARNING: history fetch failed for {key}: {exc}")
                team_history[key] = []

    # ── 3. 22bet odds (reuse client + events from step 1c) ────────────────
    _log("Reusing 22bet basketball events for odds matching...")
    client = bet22_client_early
    bet22_events = bet22_events_early
    _log(f"  {len(bet22_events)} 22bet basketball events available for odds matching")

    # ── 4. Compute features + signals for each game ───────────────────────
    output_games = []
    total_signals = 0

    for g in games_raw:
        game_date = g["date_utc"][:10]   # YYYY-MM-DD
        hf = g.get("_history_fn", "espn")
        folder = g.get("_folder", "")

        h_hist = team_history.get((g["home"]["id"], hf, folder), [])
        a_hist = team_history.get((g["away"]["id"], hf, folder), [])

        h_feats = compute_team_features(h_hist, game_date)
        a_feats = compute_team_features(a_hist, game_date)

        # Enrich team dicts with computed features
        home_out = dict(g["home"])
        away_out = dict(g["away"])
        home_out.update(h_feats)
        away_out.update(a_feats)
        # Inject game date so season-position strategies (S16/S19/S20/S21) can fire
        home_out["date_utc"] = g["date_utc"]
        away_out["date_utc"] = g["date_utc"]

        # Strategy signals
        signals = evaluate_game(home_out, away_out, h_feats, a_feats)
        ranked  = rank_signals(signals)
        top     = top_signal(signals)
        total_signals += len(signals)

        # 22bet odds
        odds = get_game_odds(g["home"]["name"], g["away"]["name"], bet22_events)

        # Determine overall card colour for UI
        if ranked:
            top_conf = top.get("confidence", "medium")
            card_color = {
                "very_high": "green",
                "high":      "green",
                "medium":    "amber",
                "low":       "grey",
            }.get(top_conf, "grey")
        else:
            card_color = "grey"

        output_games.append({
            "id":          g["id"],
            "name":        g["name"],
            "date_utc":    g["date_utc"],
            "state":       g["state"],
            "status_name": g["status_name"],
            "clock":       g["clock"],
            "period":      g["period"],
            "league":      g.get("league", "NBA"),
            "home":        home_out,
            "away":        away_out,
            "signals":     ranked,
            "top_signal":  top,
            "odds_22bet":  odds,
            "card_color":  card_color,
        })

    # Sort: scheduled first (pre), then in-progress (in), then finished (post)
    state_order = {"pre": 0, "in": 1, "post": 2}
    output_games.sort(key=lambda x: (state_order.get(x["state"], 9), x["date_utc"]))

    payload = {
        "updated_utc": now_utc.isoformat(timespec="seconds"),
        "games":       output_games,
        "meta": {
            "total_games":   len(output_games),
            "total_signals": total_signals,
            "bet22_events":  len(bet22_events),
            "season":        _guess_season(now_utc),
        },
    }

    return payload


def _guess_season(dt: datetime) -> str:
    year = dt.year
    month = dt.month
    if month >= 10:
        return f"{year}-{str(year+1)[-2:]} Regular Season"
    elif month <= 6:
        return f"{year-1}-{str(year)[-2:]} Playoffs"
    else:
        return f"{year-1}-{str(year)[-2:]} Off-Season"


def main():
    _log("=== NBA Betting Assistant — Data Update ===")
    try:
        payload = build_signals()
    except Exception as exc:
        _log(f"ERROR during build: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    meta = payload.get("meta", {})
    _log(f"Written: {OUT_FILE}")
    _log(f"  Games: {meta.get('total_games',0)}  "
         f"Signals: {meta.get('total_signals',0)}  "
         f"22bet events: {meta.get('bet22_events',0)}")


if __name__ == "__main__":
    main()
