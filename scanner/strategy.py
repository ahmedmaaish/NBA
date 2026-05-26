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
    h_rest = _safe(h_feats.get("rest"), 7)
    a_rest = _safe(a_feats.get("rest"), 7)
    h_wr   = _safe(h_feats.get("r10_win_rate"), 0.5)
    a_wr   = _safe(a_feats.get("r10_win_rate"), 0.5)
    h_wr5  = _safe(h_feats.get("r5_win_rate"),  0.5)
    a_wr5  = _safe(a_feats.get("r5_win_rate"),  0.5)
    h_diff = _safe(h_feats.get("r10_diff"), 0.0)
    a_diff = _safe(a_feats.get("r10_diff"), 0.0)
    h_szn  = home.get("season_pct", 0.5)
    a_szn  = away.get("season_pct", 0.5)

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
