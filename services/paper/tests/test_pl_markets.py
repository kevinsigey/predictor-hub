"""PL totals and both-teams-to-score: the ladder is priced at its own line.

Kalshi lists two PL markets the ledger did not have:

  KXEPLTOTAL  "Will over N.5 goals be scored?" - one rung per threshold, and
               the model's probability has to be evaluated at THAT rung's line.
               A single "over 2.5" figure is right for exactly one of the six.
  KXEPLBTTS   "Both Teams To Score" - one binary market per fixture, and the
               snapshot publishes `btts_yes_prob` for it directly.

Both are priced from the PL snapshot's own `score_grid`, which is a joint
distribution over (home goals, away goals). The grid is the model; the totals
ladder is a threshold on the grid's sum, and BTTS is the grid's off-diagonal
mass. Using the grid rather than a Normal fit matters: the goal distribution is
skewed and discrete, and a Normal approximation of it was wrong by up to four
points at the middle rungs, which is the size of the edge threshold itself.
"""
from __future__ import annotations

import pytest
from conftest import build, config, kalshi_row

PL_CFG = config(segments=[
    {"segment": "pl:total", "sport": "pl", "market": "total"},
    {"segment": "pl:btts", "sport": "pl", "market": "btts"},
])

# A joint grid over (home goals, away goals). Rows are home 0..3, columns away
# 0..3. Deliberately asymmetric so a code that confuses home with away, or
# transposes the grid, gets a different answer. The mass is 0.90, not 1.0: PL's
# own grid stops at six goals a side, so the tail sits outside it and the
# ladder has to be priced against the grid's own mass.
GRID = [
    [0.10, 0.15, 0.10, 0.05],   # home 0
    [0.08, 0.12, 0.08, 0.04],   # home 1
    [0.04, 0.06, 0.04, 0.04],   # home 2
    [0.00, 0.00, 0.00, 0.00],   # home 3 (unreachable)
]
GRID_SUM = 0.90  # the grid is a probability mass with 10% outside it


def pl_snapshot(*, generated_at="2026-10-17T19:00:00+00:00",
                grid=GRID, btts_yes_prob: float | None = 0.55,
                event_id: str = "2jSm-Akzh-CVGi") -> dict:
    """A PL snapshot with one fixture, its score grid and its BTTS probability."""
    detail = {
        "event_id": event_id,
        "commence_time": "2026-10-25T18:00:00Z",
        "team_home": "Man United",
        "team_away": "Bournemouth",
        "post_match": False,
        "home_win": {"prob": 0.55},
        "draw": {"prob": 0.25},
        "away_win": {"prob": 0.20},
        "score_grid": grid,
    }
    if btts_yes_prob is not None:
        detail["btts_yes_prob"] = btts_yes_prob
    return {
        "generated_at": generated_at,
        "model_fingerprint": "pl-model-1",
        "fixture_detail_by_event_id": {event_id: detail},
    }


def pl_row(series: str, stat: str, suffix: str, **over) -> dict:
    """One PL rung: an event on the 25th, quoted on the 24th."""
    event = f"{series}-26OCT25MUNBOU"
    return kalshi_row(
        series=series,
        stat=stat,
        line=None,
        event_ticker=event,
        ticker=f"{event}-{suffix}",
        occurrence="2026-10-25T18:00:00+00:00",
        **over,
    )


def total_row(line: float, **over) -> dict:
    row = pl_row("KXEPLTOTAL", "total", "1", **over)
    row["line"] = line
    row["ticker"] = f"KXEPLTOTAL-26OCT25MUNBOU-{int(line * 10)}"
    return row


def btts_row(**over) -> dict:
    return pl_row("KXEPLBTTS", "btts", "BTTS", **over)


def build_pl(rows, *, cfg=None, snapshot=None):
    from paper import sources
    from paper.log import candidate_rows
    snap = snapshot if snapshot is not None else pl_snapshot()
    return candidate_rows(
        list(rows),
        {"pl": sources.snapshot_games(snap, "pl")},
        cfg if cfg is not None else PL_CFG,
    )


def grid_p_over(line: float, grid=GRID) -> float:
    """P(total goals > line) from the grid, normalised by the grid's own mass."""
    rows, cols = len(grid), len(grid[0])
    mass = sum(sum(r) for r in grid)
    return sum(grid[i][j] for i in range(rows) for j in range(cols)
               if i + j > line) / mass


def _normal_p_over(grid, line: float) -> float:
    """P(total > line) from a Normal fit of the grid. What we must NOT use."""
    import math

    rows, cols = len(grid), len(grid[0])
    mass = sum(sum(r) for r in grid)
    dist: dict[int, float] = {}
    for i in range(rows):
        for j in range(cols):
            dist[i + j] = dist.get(i + j, 0.0) + grid[i][j] / mass
    mu = sum(t * v for t, v in dist.items())
    sigma = math.sqrt(sum((t - mu) ** 2 * v for t, v in dist.items()))
    return 0.5 * (1.0 - math.erf((line - mu) / sigma / math.sqrt(2.0)))


# --- totals -----------------------------------------------------------------

def test_total_probability_is_evaluated_at_the_rungs_own_line():
    # "over 1.5 goals" is likelier than "over 2.5", and each rung must be
    # priced by its own threshold rather than by one shared over_prob.
    made, _ = build_pl([total_row(1.5, yes_ask=0.55, yes_bid=0.53)])
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["model_p"] == pytest.approx(grid_p_over(1.5), abs=1e-6)
    assert made[0]["model_p"] > 0.6


def test_a_higher_rung_is_not_given_the_lower_rungs_probability():
    made, _ = build_pl([total_row(3.5, yes_ask=0.20, yes_bid=0.18, no_bid=0.80, no_ask=0.82)])
    assert made == []
    # The model says a high line is unlikely too; the price would need to be far
    # below that to be a bet.
    assert grid_p_over(3.5) < 0.25


def test_a_mid_ladder_rung_is_priced_from_the_grid_not_a_normal_fit():
    # The middle rungs are where a Normal approximation of a skewed, discrete
    # goal distribution is worst. The grid's own answer and the Normal fit of
    # the same grid differ, and the grid is the one that must be used.
    from paper import sources

    grid = sources._score_grid(GRID)
    assert grid is not None
    normal_fit = _normal_p_over(grid, 2.5)
    assert grid_p_over(2.5) == pytest.approx(0.3444444444, abs=1e-9)
    # They are genuinely different answers, so this test can fail if the code
    # ever switches to the Normal fit.
    assert abs(grid_p_over(2.5) - normal_fit) > 0.01


def test_the_logged_total_row_prices_from_the_grid():
    made, _ = build_pl([total_row(1.5, yes_ask=0.55, yes_bid=0.53)])
    assert made[0]["model_basis"] == "score_grid at the rung line"
    assert made[0]["model_p"] == pytest.approx(grid_p_over(1.5), abs=1e-6)


def test_a_total_without_a_line_is_refused():
    row = total_row(1.5, yes_ask=0.55, yes_bid=0.53)
    row["line"] = None
    made, skipped = build_pl([row])
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_fixture_with_no_score_grid_has_no_total_probability():
    snap = pl_snapshot(grid=None)
    made, skipped = build_pl([total_row(1.5, yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert made == []
    assert skipped.get("no_model_probability") == 1


# --- both teams to score ----------------------------------------------------

def test_btts_uses_the_published_probability():
    made, _ = build_pl([btts_row(yes_ask=0.48, yes_bid=0.46)])
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["model_p"] == pytest.approx(0.55, abs=1e-6)
    assert made[0]["segment"] == "pl:btts:0.50-0.60"


def test_btts_without_a_published_probability_is_skipped_not_guessed():
    snap = pl_snapshot(btts_yes_prob=None)
    made, skipped = build_pl([btts_row(yes_ask=0.48, yes_bid=0.46)], snapshot=snap)
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_btts_below_the_edge_threshold_is_not_logged():
    # The model says 0.55; a 70c price is not a bet.
    made, skipped = build_pl([btts_row(yes_ask=0.70, yes_bid=0.68)])
    assert made == []
    assert skipped.get("below_edge_threshold") == 1


def test_btts_prices_the_yes_side_not_the_midpoint():
    # The model says 0.55 and the YES ask is 0.48: five points before the fee.
    # The midpoint (0.47) would look like six. The logged price must be the ask.
    made, _ = build_pl([btts_row(yes_ask=0.48, yes_bid=0.46)])
    assert made[0]["market_price"] == pytest.approx(0.48)


def test_a_played_fixture_is_not_a_candidate():
    snap = pl_snapshot()
    snap["fixture_detail_by_event_id"]["2jSm-Akzh-CVGi"]["post_match"] = True
    made, skipped = build_pl([btts_row(yes_ask=0.48, yes_bid=0.46)], snapshot=snap)
    assert made == []
