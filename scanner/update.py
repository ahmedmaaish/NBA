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

from .espn_nba   import fetch_schedule_window, parse_event, fetch_team_recent_games, compute_team_features
from .bet22_nba  import Bet22NBAClient, get_game_odds
from .strategy   import evaluate_game, rank_signals, top_signal

OUT_FILE = Path(__file__).resolve().parent.parent / "docs" / "data" / "signals.json"


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def build_signals() -> dict:
    now_utc = datetime.now(timezone.utc)

    # ── 1. Fetch ESPN schedule (today + next 3 days) ──────────────────────
    _log("Fetching ESPN NBA schedule...")
    raw_events = fetch_schedule_window(days_ahead=3)
    _log(f"  {len(raw_events)} raw events")

    games_raw = []
    for ev in raw_events:
        parsed = parse_event(ev)
        if parsed:
            games_raw.append(parsed)
    _log(f"  {len(games_raw)} parseable games")

    if not games_raw:
        _log("No games found — writing empty signals.json")
        return {
            "updated_utc": now_utc.isoformat(timespec="seconds"),
            "games": [],
            "meta": {"msg": "No NBA games scheduled in next 3 days."},
        }

    # ── 2. Collect unique team IDs ─────────────────────────────────────────
    team_ids: set[str] = set()
    for g in games_raw:
        team_ids.add(g["home"]["id"])
        team_ids.add(g["away"]["id"])
    _log(f"Fetching recent game history for {len(team_ids)} teams...")

    # Parallel fetch team schedules
    team_history: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        future_map = {pool.submit(fetch_team_recent_games, tid, 15): tid
                      for tid in team_ids}
        for fut in as_completed(future_map):
            tid = future_map[fut]
            try:
                history = fut.result()
            except Exception as exc:
                _log(f"  WARNING: history fetch failed for {tid}: {exc}")
                history = []
            team_history[tid] = history

    # ── 3. Fetch 22bet NBA odds ────────────────────────────────────────────
    _log("Fetching 22bet basketball odds...")
    client = Bet22NBAClient(ttl=30.0)
    bet22_events = client.fetch_all()
    _log(f"  {len(bet22_events)} 22bet basketball events")

    # ── 4. Compute features + signals for each game ───────────────────────
    output_games = []
    total_signals = 0

    for g in games_raw:
        game_date = g["date_utc"][:10]   # YYYY-MM-DD

        h_hist = team_history.get(g["home"]["id"], [])
        a_hist = team_history.get(g["away"]["id"], [])

        h_feats = compute_team_features(h_hist, game_date)
        a_feats = compute_team_features(a_hist, game_date)

        # Enrich team dicts with computed features
        home_out = dict(g["home"])
        away_out = dict(g["away"])
        home_out.update(h_feats)
        away_out.update(a_feats)

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
