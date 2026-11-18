"""The NO side of the rung (2026-11-17).

Until this change the logger evaluated only the YES side of every rung,
so all 4,347 historical ledger rows are YES rows and every rung where
the model's disagreement pointed the other way was skipped. Now both
sides are evaluated at their own buyable ask and the larger
fee-adjusted edge is logged.

The frames stay straight, and these tests pin them:

  model_p   the probability of YES -- the rung's proposition. It does
            not flip for a NO row, because the calibration record
            pairs it against the contract's result.
  outcome   the contract's result, as Kalshi resolved it. Also unflipped.
  pnl / won the HOLDER's: a NO row wins exactly when the contract
            resolves "no".
"""
from __future__ import annotations

import pytest
from conftest import build, config, kalshi_row, nfl_snapshot

from paper import sources
from paper.fees import kalshi_fee_cents
from paper.ledger import Ledger, resolved
from paper.log import candidate_rows
from paper.settle import pnl_per_contract, settle


def test_a_rung_where_no_has_the_edge_logs_no():
    """Model says Baltimore wins 30% of the time; NO is offered at 62c.

    YES has no edge (0.30 against a 40c ask). NO's probability is the
    complement, 0.70, against a 62c ask: a real edge the old logger
    skipped as below threshold.
    """
    made, skipped = build([kalshi_row()], home_win=0.30)
    assert len(made) == 1
    row = made[0]
    assert row["side"] == "no"
    assert row["market_price"] == 0.62  # the NO ask, not the YES ask
    assert row["model_p"] == 0.30  # still the YES probability
    fee = kalshi_fee_cents(62.0) / 100.0
    assert row["fee"] == pytest.approx(fee)
    assert row["edge"] == pytest.approx(round((0.70 - 0.62 - fee) * 100.0, 4))
    assert row["stake"] > 0  # Kelly ran on the NO probability, 0.70


def test_yes_still_logs_when_its_edge_is_the_larger():
    made, _ = build([kalshi_row()], home_win=0.60)
    assert len(made) == 1
    assert made[0]["side"] == "yes"
    assert made[0]["market_price"] == 0.40


def test_neither_side_clearing_the_bar_still_skips():
    # YES: 0.45 vs 40c ask is ~3 points after the fee. NO: 0.55 vs 62c
    # is negative. Nothing to log on either side.
    made, skipped = build([kalshi_row()], home_win=0.45)
    assert made == []
    assert skipped.get("below_edge_threshold") == 1


def test_an_unquotable_no_side_does_not_block_a_yes_edge():
    made, _ = build([kalshi_row(no_bid=0.0, no_ask=0.99)], home_win=0.60)
    assert len(made) == 1
    assert made[0]["side"] == "yes"


def test_an_unquotable_yes_side_does_not_block_a_no_edge():
    made, _ = build([kalshi_row(yes_bid=0.0)], home_win=0.30)
    assert len(made) == 1
    assert made[0]["side"] == "no"


def _no_ledger(ledger_path):
    ledger = Ledger(ledger_path)
    made, _ = build([kalshi_row()], home_win=0.30)
    assert made[0]["side"] == "no"
    ledger.append_new(made)
    return ledger


def test_settlement_pays_a_no_row_when_the_contract_resolves_no(ledger_path):
    ledger = _no_ledger(ledger_path)
    settle(ledger, [kalshi_row(status="finalized", result="no")])
    (row,) = resolved(ledger)
    fee = kalshi_fee_cents(62.0) / 100.0
    # The record keeps the contract's result...
    assert row["outcome"] == "no"
    # ...and the holder is paid as a winner at the NO price.
    assert row["pnl_per_contract"] == pytest.approx(
        pnl_per_contract("yes", 0.62, fee))
    assert row["pnl_per_contract"] > 0


def test_settlement_charges_a_no_row_when_the_contract_resolves_yes(ledger_path):
    ledger = _no_ledger(ledger_path)
    settle(ledger, [kalshi_row(status="finalized", result="yes")])
    (row,) = resolved(ledger)
    fee = kalshi_fee_cents(62.0) / 100.0
    assert row["outcome"] == "yes"
    assert row["pnl_per_contract"] == pytest.approx(
        pnl_per_contract("no", 0.62, fee))
    assert row["pnl_per_contract"] < 0


def test_the_report_counts_a_no_row_as_won_on_a_no_result():
    from paper.report import _won
    assert _won({"side": "no", "outcome": "no"}) is True
    assert _won({"side": "no", "outcome": "yes"}) is False
    assert _won({"side": "yes", "outcome": "yes"}) is True
    assert _won({"outcome": "yes"}) is True  # a row with no side is a YES row


def test_score_rows_stay_one_per_game_and_market_on_a_no_edge_rung():
    """Score rows are the YES-frame calibration record: one per (game,
    market), priced on the YES ask, whatever side the candidate took."""
    scores: list[dict] = []
    snap = nfl_snapshot(home_win=0.30)
    made, _ = candidate_rows(
        [kalshi_row()], {"nfl": sources.snapshot_games(snap, "nfl")},
        config(), scores=scores)
    assert made[0]["side"] == "no"
    assert len(scores) == 1
    from paper.log import score_records
    records = score_records(scores)
    assert len(records) == 1
    assert records[0]["side"] == "yes"
    assert records[0]["model_p"] == 0.30
