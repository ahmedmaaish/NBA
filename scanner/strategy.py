"""NBA betting strategy signals — derived from 10-year backtester (2012-2025).

All win rates and ROI figures are out-of-sample backtest results at -110 odds,
with strict no-lookahead (rolling stats shifted by 1, season W-L shifted by 1).
Break-even win rate at -110: 52.38%.

IMPORTANT: Strategies S4-S11 show high win rates because they pick heavy
favourites. At actual sportsbook odds, the moneyline on these picks may be
-200 to -400. Most useful as:
  - Spread (ATS) signals: if conditions hold, the team is likely to cover
  - Parlay legs: combine a high-confidence pick with a totals bet
  - Line-value check: compare backtested WR to implied prob of 22bet moneyline

Strategies with realistic edge at actual moneyline odds:
  S1  (60.0% WR, 274/yr) — away B2B exhaustion: home team at -130 to -160 has +EV
  S9  (75.8% WR, 108/yr) — triple edge: signal at -150 to -200, still +EV at 76%
  S10 (63.3% WR,  56/yr) — elite away team at +EV underdog price occasionally
  S12 (64.1% WR,  81/yr) — away team with big WR edge and rest
"""
from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES = {
    "S1": {
        "name":       "Away B2B -> Bet Home",
        "win_rate":   60.0,
        "roi_pct":    14.5,
        "bets_yr":    274,
        "confidence": "medium",
        "bet":        "home",
        "note":       "Away team on back-to-back (1 day rest). Home team rested 2+ days.",
        "realistic_edge": True,
    },
    "S4": {
        "name":       "Win-Rate Gap >25% -> Bet Favourite",
        "win_rate":   68.7,
        "roi_pct":    31.2,
        "bets_yr":    479,
        "confidence": "medium",
        "bet":        "favourite",
        "note":       "10-game win-rate gap >25%. Note: likely priced as favourite.",
        "realistic_edge": False,
    },
    "S5": {
        "name":       "Home Form Edge +5pts -> Bet Home",
        "win_rate":   76.9,
        "roi_pct":    46.8,
        "bets_yr":    221,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home team avg +5pt better diff and positive form. Use for ATS/spread.",
        "realistic_edge": False,
    },
    "S6": {
        "name":       "Dominant Away vs Losing Home",
        "win_rate":   70.7,
        "roi_pct":    35.0,
        "bets_yr":    60,
        "confidence": "high",
        "bet":        "away",
        "note":       "Away team +7pt avg diff; home team negative form (<-2).",
        "realistic_edge": False,
    },
    "S7": {
        "name":       "Rest Edge + Season Lead -> Bet Home",
        "win_rate":   77.4,
        "roi_pct":    47.8,
        "bets_yr":    37,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home has 2+ day rest advantage AND >10% season win% lead. "
                      "Heavy favourite (-200 to -400). Use as spread/ATS or parlay leg.",
        "realistic_edge": False,
    },
    "S8": {
        "name":       "5-Game Hot Home vs Cold Away",
        "win_rate":   78.5,
        "roi_pct":    49.9,
        "bets_yr":    64,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home WR in last 5 >70%; away WR in last 5 <35%.",
        "realistic_edge": False,
    },
    "S9": {
        "name":       "Triple-Edge Home (Rest+WR+Diff)",
        "win_rate":   75.8,
        "roi_pct":    44.7,
        "bets_yr":    108,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home leads on rest, 10-game win rate AND point diff. "
                      "Strong ATS signal even when priced as -200 favourite.",
        "realistic_edge": True,
    },
    "S10": {
        "name":       "Elite Away Rested vs Weak Home",
        "win_rate":   63.3,
        "roi_pct":    20.9,
        "bets_yr":    56,
        "confidence": "medium",
        "bet":        "away",
        "note":       "Away WR >62%, home WR <45%, away rested 2+ days. "
                      "Away team may still be underdog — check the line.",
        "realistic_edge": True,
    },
    "S11": {
        "name":       "Form Gap >8pts -> Bet In-Form Team",
        "win_rate":   71.2,
        "roi_pct":    36.0,
        "bets_yr":    437,
        "confidence": "high",
        "bet":        "favourite",
        "note":       "Rolling 10-game avg point diff gap >8pts. Bet the better-form team.",
        "realistic_edge": False,
    },
    "S12": {
        "name":       "Away +30% WR Edge + Rested",
        "win_rate":   64.1,
        "roi_pct":    22.4,
        "bets_yr":    81,
        "confidence": "medium",
        "bet":        "away",
        "note":       "Away WR beats home by 30%+ and away team is rested (2+ days).",
        "realistic_edge": True,
    },

    # --- v2 expansion (Oct 2025 backtest extension) ---------------------------
    "S13": {
        "name":       "Mild Form Edge -> Home",
        "win_rate":   74.1,
        "roi_pct":    41.5,
        "bets_yr":    329,
        "confidence": "high",
        "bet":        "home",
        "note":       "Loosened S5: home 10g diff edge >2.5 AND home positive diff. "
                      "High signal density during regular season.",
        "realistic_edge": True,
    },
    "S14": {
        "name":       "Home Consistency Edge -> Home",
        "win_rate":   72.7,
        "roi_pct":    38.8,
        "bets_yr":    179,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home 10g diff >+5 AND 5g diff >+5. Sustained dominance, "
                      "not a fluke. Strong ATS signal.",
        "realistic_edge": True,
    },
    "S15": {
        "name":       "20g Hot Home + WR Edge -> Home",
        "win_rate":   76.5,
        "roi_pct":    46.0,
        "bets_yr":    137,
        "confidence": "high",
        "bet":        "home",
        "note":       "Home WR in last 20 games >65% AND 10g WR edge >10%. "
                      "Established hot team.",
        "realistic_edge": True,
    },
    "S16": {
        "name":       "Mid-Season Form + Season Lead -> Home",
        "win_rate":   79.2,
        "roi_pct":    51.2,
        "bets_yr":    182,
        "confidence": "very_high",
        "bet":        "home",
        "note":       "Mid-season (Dec-Mar): home diff +5 AND season win% lead >5%. "
                      "Best new strategy — high WR + decent volume.",
        "realistic_edge": True,
    },
    "S17": {
        "name":       "Cold 3-Game Home Streak -> Fade Home",
        "win_rate":   61.8,
        "roi_pct":    17.9,
        "bets_yr":    82,
        "confidence": "medium",
        "bet":        "away",
        "note":       "Home lost 2+ of last 3 AND away team is winning (10g WR >50%). "
                      "Bet the team riding momentum.",
        "realistic_edge": True,
    },
    "S18": {
        "name":       "Hot Away 5g + Rest -> Away",
        "win_rate":   61.8,
        "roi_pct":    17.9,
        "bets_yr":    66,
        "confidence": "medium",
        "bet":        "away",
        "note":       "Away 5g WR >75%, away rested 2+ days, home 5g WR <50%. "
                      "Away is hotter, fresher.",
        "realistic_edge": True,
    },
    "S19": {
        "name":       "Late-Season Big Mismatch -> Home",
        "win_rate":   85.1,
        "roi_pct":    62.5,
        "bets_yr":    6,
        "confidence": "very_high",
        "bet":        "home",
        "note":       "Late season (Mar-Jun): home season% >65% AND away season% <40%. "
                      "Rare but extremely strong.",
        "realistic_edge": False,
    },
    "S20": {
        "name":       "Late-Season Season Edge >15% -> Better",
        "win_rate":   74.0,
        "roi_pct":    41.2,
        "bets_yr":    60,
        "confidence": "high",
        "bet":        "favourite",
        "note":       "Late season (Mar-Jun): |season win% gap| >15%. Bet the team "
                      "with the better record.",
        "realistic_edge": False,
    },
    "S21": {
        "name":       "Early-Season WR Edge >25% -> Better",
        "win_rate":   67.9,
        "roi_pct":    29.6,
        "bets_yr":    95,
        "confidence": "medium",
        "bet":        "favourite",
        "note":       "Early season (Oct-Nov): |10g WR gap| >25%. Form trumps "
                      "season-record noise when sample is small.",
        "realistic_edge": True,
    },
    "S22": {
        "name":       "Mild Form Diff Gap 5-8 -> Better",
        "win_rate":   61.1,
        "roi_pct":    16.6,
        "bets_yr":    232,
        "confidence": "medium",
        "bet":        "favourite",
        "note":       "Diff edge between 5 and 8 pts. Bridge between S5 and S11 "
                      "for medium-strength signals.",
        "realistic_edge": True,
    },
    "S23": {
        "name":       "Elite vs Elite -> Home (HCA)",
        "win_rate":   60.2,
        "roi_pct":    15.0,
        "bets_yr":    70,
        "confidence": "medium",
        "bet":        "home",
        "note":       "Both teams >60% 10g WR. Home court advantage dominates. "
                      "Reliable when two top-tier teams meet.",
        "realistic_edge": True,
    },
    "S24": {
        "name":       "Tank vs Tank -> Home (HCA)",
        "win_rate":   56.0,
        "roi_pct":    6.9,
        "bets_yr":    66,
        "confidence": "low",
        "bet":        "home",
        "note":       "Both teams <35% 10g WR. Home court advantage edges. "
                      "Modest signal — confirm with line value.",
        "realistic_edge": True,
    },
}


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _safe(val, default=None):
    return val if val is not None else default


def evaluate_game(home: dict, away: dict,
                  h_feats: dict, a_feats: dict) -> list[dict]:
    """
    Apply all strategies to one game.

    home / away : parsed ESPN game competitor dicts
      {'id', 'name', 'abbr', 'wins', 'losses', 'season_pct', ...}

    h_feats / a_feats : team feature dicts from compute_team_features()
      {'rest', 'r10_win_rate', 'r10_diff', 'r5_win_rate', 'r5_diff', 'games_used'}

    Returns list of fired signal dicts.
    """
    h_rest  = _safe(h_feats.get("rest"), 7)
    a_rest  = _safe(a_feats.get("rest"), 7)
    h_wr    = _safe(h_feats.get("r10_win_rate"), 0.5)
    a_wr    = _safe(a_feats.get("r10_win_rate"), 0.5)
    h_wr5   = _safe(h_feats.get("r5_win_rate"),  0.5)
    a_wr5   = _safe(a_feats.get("r5_win_rate"),  0.5)
    h_wr3   = _safe(h_feats.get("r3_win_rate"),  0.5)
    a_wr3   = _safe(a_feats.get("r3_win_rate"),  0.5)
    h_wr20  = _safe(h_feats.get("r20_win_rate"), 0.5)
    a_wr20  = _safe(a_feats.get("r20_win_rate"), 0.5)
    h_diff  = _safe(h_feats.get("r10_diff"), 0.0)
    a_diff  = _safe(a_feats.get("r10_diff"), 0.0)
    h_diff5 = _safe(h_feats.get("r5_diff"),  0.0)
    a_diff5 = _safe(a_feats.get("r5_diff"),  0.0)
    h_szn   = home.get("season_pct", 0.5)
    a_szn   = away.get("season_pct", 0.5)

    # Season-position flag derived from the game date (live scanner passes it via
    # home/away dicts as the dict has 'date_utc' upstream, but the strategy
    # itself derives it from the home dict's enriched 'date_utc' if available).
    import datetime as _dt
    season_month = None
    date_str = home.get("date_utc") or away.get("date_utc")
    if date_str:
        try:
            m = _dt.datetime.fromisoformat(date_str.replace("Z","+00:00")).month
            # Oct=1, Nov=2, Dec=3, Jan=4, Feb=5, Mar=6, Apr=7, May=8, Jun=9
            season_month = ((m - 10) % 12) + 1
        except Exception:
            season_month = None
    early_season = season_month in (1, 2)            # Oct, Nov
    mid_season   = season_month in (3, 4, 5, 6)      # Dec, Jan, Feb, Mar
    late_season  = season_month in (7, 8, 9)         # Apr, May, Jun

    # Need minimum game history to fire strategies
    if (h_feats.get("games_used") or 0) < 3 or (a_feats.get("games_used") or 0) < 3:
        return []

    signals = []

    def fire(sid: str, override_bet: Optional[str] = None):
        s = dict(STRATEGIES[sid])
        if override_bet:
            s["bet"] = override_bet
        signals.append(s)

    # S1: Away B2B, home rested
    if a_rest == 1 and h_rest >= 2:
        fire("S1")

    # S4: Large win-rate gap
    gap = abs(h_wr - a_wr)
    if gap > 0.25:
        bet = "home" if h_wr > a_wr else "away"
        fire("S4", override_bet=bet)

    # S5: Home form edge +5 pts and positive diff
    if (h_diff - a_diff) > 5 and h_diff > 3:
        fire("S5")

    # S6: Dominant away vs losing home
    if a_diff > 7 and h_diff < -2:
        fire("S6")

    # S7: Rest edge + season win% gap
    if (h_rest - a_rest) >= 2 and (h_szn - a_szn) > 0.10:
        fire("S7")

    # S8: Hot home vs cold away (5-game)
    if h_wr5 > 0.70 and a_wr5 < 0.35:
        fire("S8")

    # S9: Triple-edge home — must STRICTLY beat away on rest (matches backtest exactly)
    if (h_rest - a_rest) >= 1 and (h_wr - a_wr) > 0.15 and (h_diff - a_diff) > 3:
        fire("S9")

    # S10: Elite away rested vs weak home
    if a_wr > 0.62 and h_wr < 0.45 and a_rest >= 2:
        fire("S10")

    # S11: Form gap >8pts
    diff_gap = h_diff - a_diff
    if abs(diff_gap) > 8:
        bet = "home" if diff_gap > 0 else "away"
        fire("S11", override_bet=bet)

    # S12: Away dominates by >30% WR + rested
    if (a_wr - h_wr) > 0.30 and a_rest >= 2:
        fire("S12")

    # -- v2 strategies (backtested 2012-2025 with shifted szn_pct) -----------

    # S13: Mild form edge (loosened S5)
    if (h_diff - a_diff) > 2.5 and h_diff > 1:
        fire("S13")

    # S14: Home consistency edge — 10g AND 5g diff both >+5
    if h_diff > 5 and h_diff5 > 5:
        fire("S14")

    # S15: 20-game hot home + 10g WR edge
    if h_wr20 > 0.65 and (h_wr - a_wr) > 0.10:
        fire("S15")

    # S16: Mid-season form edge + season lead
    if mid_season and (h_diff - a_diff) > 5 and (h_szn - a_szn) > 0.05:
        fire("S16")

    # S17: Cold 3-game home streak — fade home if away winning overall
    if h_wr3 <= 0.33 and a_wr > 0.50:
        fire("S17")

    # S18: Hot away 5g + rested
    if a_wr5 > 0.75 and a_rest >= 2 and h_wr5 < 0.50:
        fire("S18")

    # S19: Late-season big mismatch
    if late_season and h_szn > 0.65 and a_szn < 0.40:
        fire("S19")

    # S20: Late-season season edge >15% — bet better team
    if late_season and abs(h_szn - a_szn) > 0.15:
        bet = "home" if h_szn > a_szn else "away"
        fire("S20", override_bet=bet)

    # S21: Early-season WR edge >25%
    if early_season and abs(h_wr - a_wr) > 0.25:
        bet = "home" if h_wr > a_wr else "away"
        fire("S21", override_bet=bet)

    # S22: Mid-strength diff gap (5-8 pts)
    diff_gap = h_diff - a_diff
    if 5 < abs(diff_gap) <= 8:
        bet = "home" if diff_gap > 0 else "away"
        fire("S22", override_bet=bet)

    # S23: Elite vs Elite — HCA edge
    if h_wr > 0.60 and a_wr > 0.60:
        fire("S23")

    # S24: Tank vs Tank — modest HCA edge
    if h_wr < 0.35 and a_wr < 0.35:
        fire("S24")

    return signals


# ---------------------------------------------------------------------------
# Signal ranking (for display ordering)
# ---------------------------------------------------------------------------

_CONF_ORDER = {"very_high": 0, "high": 1, "medium": 2, "low": 3}


def rank_signals(signals: list[dict]) -> list[dict]:
    """Sort signals by confidence then ROI descending."""
    return sorted(
        signals,
        key=lambda s: (_CONF_ORDER.get(s.get("confidence", "low"), 9),
                       -s.get("roi_pct", 0)),
    )


def top_signal(signals: list[dict]) -> Optional[dict]:
    """Return the highest-ranked signal, or None."""
    ranked = rank_signals(signals)
    return ranked[0] if ranked else None
