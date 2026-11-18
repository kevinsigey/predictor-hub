"""Depth and fill: was there enough on offer at our ask to fill our size?

The collectors record `yes_ask_size` / `no_ask_size` (contracts resting at the
best ask; Kalshi's NO ask is backed by the YES bids, so `no_ask_size` is the
YES bid size). A row whose book could not fill the contracts we wanted is
marked `fill_short` and kept out of P&L; it is shown separately instead.
Rows from snapshots that carried no size record `ask_size: null` and
`fill_short: null` -- unknown, never assumed filled or short.
"""
from __future__ import annotations

import pytest
from conftest import build, config, kalshi_row

from paper.report import build as build_report
from paper.segments import gate


def test_a_candidate_records_the_size_at_the_bought_sides_ask():
    (row,), _ = build([kalshi_row(yes_ask_size=500.0, no_ask_size=3.0)], home_win=0.60)
    assert row["side"] == "yes"
    assert row["ask_size"] == 500.0
    assert row["contracts"] == pytest.approx(row["stake"] / 0.40, abs=1e-3)
    assert row["fill_short"] is False


def test_a_no_row_reads_the_no_sides_size_and_is_short_when_the_book_is_thin():
    (row,), _ = build([kalshi_row(yes_ask_size=500.0, no_ask_size=3.0)], home_win=0.30)
    assert row["side"] == "no"
    assert row["ask_size"] == 3.0
    assert row["contracts"] > 3.0
    assert row["fill_short"] is True


def test_no_size_in_the_snapshot_is_recorded_as_unknown():
    (row,), _ = build([kalshi_row()], home_win=0.60)
    assert row["ask_size"] is None and row["fill_short"] is None


def test_a_shadow_is_sized_as_the_bet_it_would_have_been():
    (row,), _ = build([kalshi_row(yes_ask_size=1.0)], home_win=0.60,
                      generated_at="2026-10-18T00:00:00+00:00")  # model after price: shadow
    assert row["kind"] == "shadow" and row["stake"] == 0.0
    assert row["contracts"] > 1.0 and row["fill_short"] is True


def _settled(i, *, pnl, fill_short):
    return {"record": "candidate", "id": f"r{i}", "kind": "candidate", "sport": "nfl",
            "market": "moneyline", "game_id": f"g{i}", "ticker": f"T{i}", "side": "yes",
            "segment": "nfl:moneyline:0.60-0.70", "model_p": 0.65, "market_price": 0.5,
            "edge": 10.0, "stake": 5.0, "status": "settled", "outcome": "yes" if pnl > 0 else "no",
            "pnl_per_contract": pnl, "logged_at": f"2026-10-1{i}T00:00:00+00:00",
            "price_observed_at": f"2026-10-1{i}T00:00:00+00:00", "fill_short": fill_short}


def test_the_report_keeps_fill_short_rows_out_of_pnl_and_shows_them_separately():
    rows = [_settled(1, pnl=0.48, fill_short=False), _settled(2, pnl=-0.52, fill_short=True),
            _settled(3, pnl=0.48, fill_short=None)]
    rep = build_report(rows, closing={}, gate_cfg=gate(config()))
    assert rep["totals"]["n_logged"] == 2
    assert rep["per_sport"]["nfl"]["pnl"] == pytest.approx(0.96)
    fills = rep["fills"]
    assert fills["n_depth_known"] == 2 and fills["n_fill_short"] == 1
    assert fills["fill_rate"] == 0.5
    assert fills["set_aside"]["fill_short"]["n_settled"] == 1
    assert fills["set_aside"]["fill_short"]["pnl"] == pytest.approx(-0.52)
