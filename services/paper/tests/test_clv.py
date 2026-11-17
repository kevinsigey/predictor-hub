"""Closing-line value: how much better the logged price was than the close.

CLV is a LEADING indicator. It is knowable the moment the market's last
pre-kickoff price is known, before the game is played, which is what makes it
worth looking at: it says whether the entry was early, and an edge that only
shows up after the result is luck.

Three rules the spec states, and this file pins each:

  * the closing price is the LAST snapshot strictly before kickoff, on the
    SAME side as the row, and a snapshot at or after kickoff is ignored;
  * a row with no pre-kickoff snapshot after its own log has CLV = null, which
    is counted and never zero;
  * CLV is reported beside the result and is NEVER a gate: the 300-settled
    floor, the interval and the calibration condition are untouched by it, and
    no segment may be called "has an edge" because of it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from paper import stats
from paper.report import build, summarise
from paper.segments import gate as gate_of

from conftest import config

GATE = gate_of(config())

KICKOFF = "2026-10-18T17:00:00+00:00"


def observed(*, snapshot_ts: str, ticker: str = "KXNFLGAME-26OCT18BALCLE-BAL",
              yes_ask=0.50, no_ask=0.52, occurrence: str = KICKOFF) -> dict:
    """One Kalshi observation, as `annotate_observed` would prepare it."""
    from paper import sources
    row = {
        "snapshot_ts": snapshot_ts,
        "ticker": ticker,
        "occurrence": occurrence,
        "close_time": "2026-10-20T17:00:00+00:00",   # days later, on purpose
        "yes_ask": yes_ask, "yes_bid": round(yes_ask - 0.02, 4),
        "no_ask": no_ask, "no_bid": round(no_ask - 0.02, 4),
    }
    copy = dict(row)
    copy["_seen_at"] = sources.parse_ts(snapshot_ts)
    copy["_kickoff"] = sources.kickoff(row)
    copy["_price"] = sources.buyable_price(row, "yes")
    copy["_no_price"] = sources.buyable_price(row, "no")
    return copy


def ledger_row(**over) -> dict:
    """A logged ledger row, open or settled."""
    base = {
        "record": "candidate", "kind": "candidate", "id": "id0",
        "sport": "nfl", "market": "moneyline",
        "game_id": "g", "ticker": "KXNFLGAME-26OCT18BALCLE-BAL", "selection": "BAL",
        "side": "yes", "line": None,
        "model_p": 0.60, "market_price": 0.50, "fee": 0.02, "edge": 8.0,
        "segment": "nfl:moneyline:0.60-0.70", "stake": 5.0,
        "status": "open", "outcome": None, "pnl_per_contract": None,
        "price_observed_at": "2026-10-17T20:00:00+00:00", "kickoff": KICKOFF,
        "expected_end": "2026-10-18T20:00:00+00:00",  # post-fix row: kickoff is the true start
    }
    base.update(over)
    return base


# --- which snapshot is the close ---------------------------------------------

def test_the_closing_price_is_the_last_snapshot_before_kickoff():
    rows = [
        observed(snapshot_ts="2026-10-17T18:00:00+00:00", yes_ask=0.40),
        observed(snapshot_ts="2026-10-17T22:00:00+00:00", yes_ask=0.44),
        observed(snapshot_ts="2026-10-18T10:00:00+00:00", yes_ask=0.47),
    ]
    closing = stats.closing_prices(rows)
    assert closing["KXNFLGAME-26OCT18BALCLE-BAL"]["_price"] == pytest.approx(0.47)


def test_a_snapshot_at_or_after_kickoff_is_ignored():
    rows = [
        observed(snapshot_ts="2026-10-18T16:00:00+00:00", yes_ask=0.44),
        # At kickoff, and long after: Kalshi keeps trading these markets, and a
        # post-final price is not a closing line.
        observed(snapshot_ts="2026-10-18T17:00:00+00:00", yes_ask=0.90),
        observed(snapshot_ts="2026-10-19T12:00:00+00:00", yes_ask=0.99),
    ]
    closing = stats.closing_prices(rows)
    assert closing["KXNFLGAME-26OCT18BALCLE-BAL"]["_price"] == pytest.approx(0.44)


def test_the_close_is_the_buyable_price_not_the_midpoint():
    rows = [observed(snapshot_ts="2026-10-18T10:00:00+00:00", yes_ask=0.47, no_ask=0.55)]
    closing = stats.closing_prices(rows)
    assert closing["KXNFLGAME-26OCT18BALCLE-BAL"]["_price"] == pytest.approx(0.47)


# --- the sign -----------------------------------------------------------------

def test_clv_is_positive_when_we_beat_the_close():
    # We logged 40c and the market closed at 45c: we got in before the market
    # did, which is the part of an edge that should persist.
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.45, "_no_price": 0.45}}
    row = ledger_row(market_price=0.40)
    assert stats.clv_points(row, closing) == pytest.approx(5.0)


def test_clv_is_negative_when_we_paid_more_than_the_close():
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.45, "_no_price": 0.45}}
    row = ledger_row(market_price=0.50)
    assert stats.clv_points(row, closing) == pytest.approx(-5.0)


def test_a_no_row_is_priced_against_the_no_side():
    # The close for a NO row is the NO price, and the sign convention is the
    # same: closing implied probability MINUS the logged price. A NO row logged
    # at 40c that closes at 45c is +5 points, because we bought the NO side
    # 5 points cheaper than the market's last word on it.
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.55, "_no_price": 0.45}}
    row = ledger_row(side="no", market_price=0.40)
    assert stats.clv_points(row, closing) == pytest.approx(5.0)


def test_a_no_row_is_not_priced_against_the_yes_side():
    # The YES price here is 0.55, so a wrong implementation would report
    # +15 points instead of +5.
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.55, "_no_price": 0.45}}
    row = ledger_row(side="no", market_price=0.40)
    assert stats.clv_points(row, closing) != pytest.approx(15.0)


# --- missing closes -----------------------------------------------------------

def test_a_row_with_no_closing_price_has_null_clv_not_zero():
    row = ledger_row()
    assert stats.clv_points(row, {}) is None


def test_a_row_whose_only_snapshot_predates_its_log_has_null_clv():
    # The close has to be observed at or after our own entry: a snapshot from
    # before we got there is not evidence about the close.
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": datetime(2026, 10, 17, 12, tzinfo=timezone.utc),
        "_kickoff": datetime(2026, 10, 18, 17, tzinfo=timezone.utc),
        "_price": 0.45, "_no_price": 0.45}}
    row = ledger_row(price_observed_at="2026-10-17T20:00:00+00:00")
    assert stats.clv_points(row, closing) is None


def test_a_close_observed_at_the_same_moment_as_the_log_counts():
    # Equal is not "before": the snapshot we logged from is itself a pre-kickoff
    # observation, so it can be the close.
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": datetime(2026, 10, 17, 20, tzinfo=timezone.utc),
        "_kickoff": datetime(2026, 10, 18, 17, tzinfo=timezone.utc),
        "_price": 0.55, "_no_price": 0.55}}
    row = ledger_row(price_observed_at="2026-10-17T20:00:00+00:00", market_price=0.50)
    assert stats.clv_points(row, closing) == pytest.approx(5.0)


# --- what the report says ------------------------------------------------------

def _seg(rows, closing):
    report = build(rows, closing=closing, gate_cfg=GATE)
    return report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]


def test_clv_is_reported_over_every_logged_row_not_only_the_settled_ones():
    # CLV is a LEADING indicator, so an OPEN row has one too. Reporting it
    # only over settled rows would hide the signal exactly when it is useful.
    rows = [ledger_row(id="a", status="open", outcome=None, pnl_per_contract=None),
            ledger_row(id="b", status="settled", outcome="yes", pnl_per_contract=0.48)]
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.55, "_no_price": 0.55}}
    s = _seg(rows, closing)
    assert s["n_clv"] == 2


def test_the_report_gives_the_share_of_rows_with_positive_clv():
    rows = [
        ledger_row(id="a", market_price=0.40),   # +5: we beat the close
        ledger_row(id="b", market_price=0.50),   # -5: we paid more
        ledger_row(id="c", market_price=0.45),   #  0: exactly the close
    ]
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.45, "_no_price": 0.45}}
    s = _seg(rows, closing)
    assert s["mean_clv_cents"] == pytest.approx(0.0)
    assert s["share_clv_positive"] == pytest.approx(1 / 3, abs=1e-4)


def test_a_missing_close_is_counted_but_not_scored_as_zero():
    rows = [
        ledger_row(id="a", ticker="KXNFLGAME-26OCT18BALCLE-BAL"),
        ledger_row(id="b", ticker="KXNFLGAME-26OCT18BALCLE-CLE"),
    ]
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {
        "_seen_at": 1, "_kickoff": 2, "_price": 0.55, "_no_price": 0.55}}
    s = _seg(rows, closing)
    # One row has a close and one does not. The one without must not drag the
    # mean to zero, and must not count as a non-positive row either.
    assert s["n_clv"] == 1
    assert s["mean_clv_cents"] == pytest.approx(5.0)
    assert s["share_clv_positive"] == pytest.approx(1.0)


# --- CLV is never a gate -------------------------------------------------------

def test_clv_does_not_affect_whether_a_segment_qualifies():
    rows = [ledger_row(id=f"id{i}", game_id=f"g{i}", ticker=f"T{i}") for i in range(300)]
    rows = [dict(r, status="settled", outcome="yes", pnl_per_contract=0.48) for r in rows]
    good_close = {"T0": {"_seen_at": 1, "_kickoff": 2, "_price": 0.99, "_no_price": 0.99}}
    bad_close = {"T0": {"_seen_at": 1, "_kickoff": 2, "_price": 0.01, "_no_price": 0.01}}
    assert _seg(rows, good_close)["qualifies_for_edge_claim"] == \
        _seg(rows, bad_close)["qualifies_for_edge_claim"]


def test_clv_never_adds_a_disqualifying_reason():
    rows = [ledger_row(id=f"id{i}", game_id=f"g{i}", ticker=f"T{i}") for i in range(5)]
    closing = {"T0": {"_seen_at": 1, "_kickoff": 2, "_price": 0.99, "_no_price": 0.99}}
    s = _seg(rows, closing)
    assert not any("clv" in reason.lower() for reason in s["disqualifying"])


def test_the_gate_values_are_unchanged_by_any_of_this():
    # The spec states the gate stays exactly as it is.
    assert GATE["min_settled"] == 300
    assert GATE["require_ci_excludes_zero"] is True
    assert GATE["require_calibration_not_widening"] is True
