"""The first registered shadow rule, pinned as written before its data."""
from __future__ import annotations

from pathlib import Path

import pytest

from paper import rules

RULES = Path(__file__).resolve().parents[3] / "data" / "paper" / "rules"


def test_the_cfb_rule_is_registered_exactly_as_preregistered():
    specs, kills = rules.load(RULES)
    (spec,) = [s for s in specs if s["id"] == "cfb-moneyline-early-disagreement"]
    assert (spec["sport"], spec["market"], spec["population"]) == ("cfb", "moneyline", "score")
    assert spec["parameters"] == {"min_disagreement": 0.10, "min_lead_hours": 24, "max_spread_points": 8.0}
    assert spec["promotion"]["min_n"] == 150
    assert spec["kill"] == {"max_days": 60, "max_trades": 300}
    assert spec["prereg_at"] >= spec["written_at"]
    assert not [k for k in kills if k["rule_id"] == spec["id"]]


def test_prereg_cannot_be_backdated_before_the_rule_was_written():
    specs, _ = rules.load(RULES)
    spec = dict(specs[0], prereg_at="2026-10-01T00:00:00Z")
    with pytest.raises(ValueError):
        rules.validate(spec)


def test_no_nba_prop_rule_is_registered_yet():
    specs, _ = rules.load(RULES)
    assert not [s for s in specs if s["sport"] == "nba" and s["market"] not in ("moneyline", "spread", "total")]
