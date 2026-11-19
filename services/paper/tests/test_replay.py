"""Replay discipline: replayed rows pay a 1c haircut and never rank or promote alone."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import config

from paper import rules
from paper.ledger import REPLAY_HAIRCUT, is_replay
from paper.report import build as build_report, promotion_test
from paper.segments import gate


def bet(i, pnl, **over):
    row = {"record": "candidate", "id": f"b{i}", "kind": "candidate", "sport": "pl", "market": "moneyline",
           "game_id": f"g{i}", "ticker": f"T{i}", "side": "yes", "segment": "pl:moneyline:0.50-0.60",
           "model_p": 0.55, "market_price": 0.45, "edge": 6.0, "stake": 5.0, "status": "settled",
           "outcome": "yes" if pnl > 0 else "no", "pnl_per_contract": pnl,
           "logged_at": f"2026-10-{10 + i % 9}T00:00:00+00:00",
           "price_observed_at": f"2026-10-{10 + i % 9}T00:00:00+00:00"}
    row.update(over)
    return row


def test_the_haircut_is_one_cent_and_replay_is_recognised():
    assert REPLAY_HAIRCUT == 0.01
    assert is_replay({"backtest": True}) and is_replay({"exploratory": True})
    assert not is_replay(bet(1, 0.5))


def test_replay_rows_never_enter_the_segment_table_or_verdicts():
    replay = [bet(i, 0.5, backtest=True, exploratory=True) for i in range(5)]
    rep = build_report(replay, closing={}, gate_cfg=gate(config()))
    assert rep["segments"] == {} and rep["totals"]["n_logged"] == 0
    assert rep["totals"]["excluded_replay_rows"] == 5


def test_replay_pays_the_haircut_and_the_report_says_whether_it_agrees_with_forward():
    replay = [bet(i, 0.005, backtest=True) for i in range(20)]  # +0.5c raw, -0.5c after the haircut
    forward = [bet(100 + i, 0.10) for i in range(20)]
    rep = build_report(forward, closing={}, gate_cfg=gate(config()), replay_rows=replay)
    cell = rep["replay"]["pl:moneyline"]
    assert cell["replay_net_cents_per_contract"] == pytest.approx(-0.5)
    assert cell["forward_net_cents_per_contract"] == pytest.approx(10.0)
    assert cell["agree_in_direction"] is False


def test_replay_score_rows_never_count_toward_a_shadow_rule():
    spec = {"id": "r", "description": "", "population": "score", "sport": "cfb", "market": "moneyline",
            "metric": "", "parameters": {"min_disagreement": 0.1, "min_lead_hours": 24, "max_spread_points": 8},
            "prereg_at": "2026-11-20T00:00:00Z", "promotion": {"min_n": 150},
            "kill": {"max_days": 60, "max_trades": 300}}
    row = {"sport": "cfb", "market": "moneyline", "game_id": "g", "model_p": 0.8, "market_price": 0.5,
           "market_mid": 0.49, "price_observed_at": "2026-11-21T00:00:00+00:00",
           "kickoff": "2026-11-23T00:00:00+00:00", "outcome": "yes"}
    assert len(rules.trades(spec, [row])) == 1
    assert rules.trades(spec, [dict(row, backtest=True)]) == []
