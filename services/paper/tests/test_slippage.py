"""Slippage cap: a paper fill the market had already moved away from is skipped.

The decision price is frozen when the row is logged. The fill is modelled at
the next observation of the same ticker after the decision (and before the
start): if the bought side's ask there is more than 3c worse than the frozen
price, the row's status is `slippage_cap` and it is kept out of P&L. Rows that
do fill are graded at the FROZEN decision price, never the later one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import build, config, kalshi_row

from paper import sources, stats
from paper.report import SLIPPAGE_CAP, build as build_report, fill_observations, set_aside_fills
from paper.segments import gate

LATER = "2026-10-17T20:10:00+00:00"  # inside the 15-minute fill window


def _settled(row, result):
    from paper.settle import pnl_per_contract
    holder = result if row["side"] == "yes" else {"yes": "no", "no": "yes"}[result]
    return dict(row, status="settled", outcome=result,
                pnl_per_contract=pnl_per_contract(holder, row["market_price"], row["fee"]))


def _later(**quote):
    obs = stats.annotate_observed([kalshi_row(snapshot_ts=LATER, **quote)])
    for o in obs:
        o["_kickoff"] = sources.parse_ts("2026-10-18T17:00:00+00:00")
    return fill_observations(obs)


def test_the_cap_is_three_cents():
    assert SLIPPAGE_CAP == 0.03


@pytest.mark.parametrize("later_ask,capped", [(0.44, True), (0.43, False), (0.30, False)])
def test_yes_row_is_capped_when_the_yes_ask_moved_more_than_3c_against_us(later_ask, capped):
    (row,), _ = build([kalshi_row()], home_win=0.60)
    assert row["side"] == "yes" and row["market_price"] == 0.40
    kept, fills = set_aside_fills([row], _later(yes_ask=later_ask, yes_bid=later_ask - 0.02))
    assert (kept == []) is capped
    assert fills["set_aside"]["slippage_cap"]["n"] == int(capped)


@pytest.mark.parametrize("later_no_ask,capped", [(0.66, True), (0.65, False)])
def test_no_row_is_capped_on_the_no_ask_not_the_yes_ask(later_no_ask, capped):
    (row,), _ = build([kalshi_row()], home_win=0.30)  # #187: NO at 62c
    assert row["side"] == "no" and row["market_price"] == 0.62
    # The YES quote is left where it was; only NO's ask moves.
    kept, fills = set_aside_fills([row], _later(no_ask=later_no_ask, no_bid=later_no_ask - 0.02))
    assert (kept == []) is capped


def test_no_later_observation_before_the_start_means_no_cap():
    (row,), _ = build([kalshi_row()], home_win=0.60)
    after_start = stats.annotate_observed([kalshi_row(snapshot_ts="2026-10-18T18:00:00+00:00",
                                                      yes_ask=0.90, yes_bid=0.88)])
    for o in after_start:
        o["_kickoff"] = sources.parse_ts("2026-10-18T17:00:00+00:00")
    kept, fills = set_aside_fills([row], fill_observations(after_start))
    assert kept == [row] and fills["n_fill_unobserved"] == 1


def test_a_filled_row_is_graded_at_the_frozen_decision_price():
    (row,), _ = build([kalshi_row()], home_win=0.60)
    row = _settled(row, "yes")
    rep = build_report([row], closing={}, gate_cfg=gate(config()),
                       fill_obs=_later(yes_ask=0.42, yes_bid=0.40))
    # 1 - 0.40 - 0.02 fee, not 1 - 0.42 - fee.
    assert rep["per_sport"]["nfl"]["pnl"] == pytest.approx(0.58)
    assert rep["fills"]["set_aside"]["slippage_cap"]["n"] == 0


def test_a_capped_no_row_is_shown_separately_at_its_decision_price():
    (row,), _ = build([kalshi_row()], home_win=0.30)
    row = _settled(row, "no")
    rep = build_report([row], closing={}, gate_cfg=gate(config()),
                       fill_obs=_later(no_ask=0.70, no_bid=0.68))
    assert rep["totals"]["n_logged"] == 0
    aside = rep["fills"]["set_aside"]["slippage_cap"]
    assert aside["n_settled"] == 1 and aside["pnl"] == pytest.approx(1 - 0.62 - row["fee"])


def test_a_move_seen_hours_later_is_information_not_slippage():
    """A quote outside the fill window says where the market went, not what we
    could have filled at; capping on it would drop exactly the losing bets."""
    (row,), _ = build([kalshi_row()], home_win=0.60)
    obs = stats.annotate_observed([kalshi_row(snapshot_ts="2026-10-18T08:00:00+00:00",
                                              yes_ask=0.60, yes_bid=0.58)])
    for o in obs:
        o["_kickoff"] = sources.parse_ts("2026-10-18T17:00:00+00:00")
    kept, fills = set_aside_fills([row], fill_observations(obs))
    assert kept == [row] and fills["n_fill_unobserved"] == 1
