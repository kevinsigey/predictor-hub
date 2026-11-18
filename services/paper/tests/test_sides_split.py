"""The report's YES/NO split, which the page draws."""
from __future__ import annotations

import pytest
from conftest import config

from paper.report import build
from paper.segments import gate


def row(i, side, outcome, pnl):
    return {"record": "candidate", "id": f"r{i}", "kind": "candidate", "sport": "nba", "market": "moneyline",
            "game_id": f"g{i}", "ticker": f"T{i}", "side": side, "segment": "nba:moneyline:0.60-0.70",
            "model_p": 0.6, "market_price": 0.5, "edge": 5.0, "stake": 1.0, "status": "settled",
            "outcome": outcome, "pnl_per_contract": pnl, "logged_at": "2026-11-01T00:00:00+00:00",
            "price_observed_at": "2026-11-01T00:00:00+00:00"}


def test_split_counts_each_side_and_scores_the_holder():
    rows = [row(1, "yes", "yes", 0.48), row(2, "no", "no", 0.48), row(3, "no", "yes", -0.52)]
    rep = build(rows, closing={}, gate_cfg=gate(config()))
    split = rep["per_sport"]["nba"]["by_side"]
    assert split["yes"] == {"n": 1, "n_settled": 1, "hit_rate": 1.0, "net_cents_per_contract": 48.0}
    assert split["no"]["n"] == 2 and split["no"]["hit_rate"] == 0.5
    assert split["no"]["net_cents_per_contract"] == pytest.approx(-2.0)
    assert rep["totals"]["by_side"]["no"]["n"] == 2
