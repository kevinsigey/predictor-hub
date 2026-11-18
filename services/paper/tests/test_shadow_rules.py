"""Preregistered shadow rules: only post-PREREG rows count, kills are terminal."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import config

from paper import rules
from paper.report import build as build_report, promotion_test
from paper.segments import gate

PREREG = "2026-11-20T00:00:00Z"
NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)


def spec(**over):
    base = {"id": "test-rule", "description": "test", "population": "score", "sport": "cfb",
            "market": "moneyline", "metric": "net c/contract",
            "parameters": {"min_disagreement": 0.10, "min_lead_hours": 24, "max_spread_points": 8.0},
            "prereg_at": PREREG, "promotion": {"min_n": 150},
            "kill": {"max_days": 60, "max_trades": 300}}
    base.update(over)
    return base


def score(i, *, decided, model_p=0.70, ask=0.52, mid=0.51, outcome="yes", lead_h=48):
    at = datetime.fromisoformat(decided.replace("Z", "+00:00"))
    return {"record": "score", "sport": "cfb", "market": "moneyline", "game_id": f"g{i}",
            "ticker": f"T{i}", "model_p": model_p, "market_price": ask, "market_mid": mid,
            "logged_at": at.isoformat(), "price_observed_at": at.isoformat(),
            "kickoff": (at + timedelta(hours=lead_h)).isoformat(), "outcome": outcome}


def evaluate(scores, kills=(), now=NOW, s=None):
    return rules.evaluate([s or spec()], list(kills), scores, [], now=now, promotion_test=promotion_test)


def test_rows_decided_at_or_before_prereg_are_excluded():
    before = score(1, decided="2026-11-19T12:00:00Z")
    at = score(2, decided=PREREG)
    after = score(3, decided="2026-11-21T00:00:00Z")
    made = rules.trades(spec(), [before, at, after])
    assert [t["game_id"] for t in made] == ["g3"]


def test_the_rule_takes_the_models_side_at_that_sides_own_ask():
    yes, no = rules.trades(spec(), [score(1, decided="2026-11-21T00:00:00Z"),
                                     score(2, decided="2026-11-21T00:00:00Z", model_p=0.30, outcome="no")])
    assert yes["side"] == "yes" and yes["price"] == 0.52
    # bid = 2 * 0.51 - 0.52 = 0.50, so NO's ask is 0.50.
    assert no["side"] == "no" and no["price"] == 0.50
    assert no["pnl_per_contract"] == pytest.approx(1 - 0.50 - no["fee"])


def test_small_disagreement_wide_book_and_late_decisions_do_not_trade():
    rows = [score(1, decided="2026-11-21T00:00:00Z", model_p=0.58),
            score(2, decided="2026-11-21T00:00:00Z", ask=0.94, mid=0.485),
            score(3, decided="2026-11-21T00:00:00Z", lead_h=5)]
    assert rules.trades(spec(), rows) == []


def test_a_rule_at_its_kill_limit_without_passing_is_killed_and_recorded():
    rows = [score(i, decided="2026-11-21T00:00:00Z", outcome="no") for i in range(5)]
    out, new = evaluate(rows, now=datetime(2026, 1, 20, tzinfo=timezone.utc) + timedelta(days=365))
    assert out["test-rule"]["status"] == "killed"
    assert new and new[0]["rule_id"] == "test-rule"
    out, _ = evaluate(rows)
    assert out["test-rule"]["status"] == "collecting"


def test_a_killed_rule_cannot_revive_on_any_later_evidence():
    kill = {"record": "rule_kill", "rule_id": "test-rule", "killed_at": "2026-12-01T00:00:00+00:00"}
    great = [score(i, decided="2026-11-21T00:00:00Z", model_p=0.95, outcome="yes") for i in range(200)]
    out, new = evaluate(great, kills=[kill])
    assert out["test-rule"]["promotion"]["legs"]["n"] is True
    assert out["test-rule"]["status"] == "killed" and new == []


def test_kill_limits_cannot_be_loosened():
    with pytest.raises(ValueError):
        rules.validate(spec(kill={"max_days": 90, "max_trades": 300}))
    with pytest.raises(ValueError):
        rules.validate(spec(prereg_at="2026-11-20T00:00:00"))


def test_kills_are_appended_and_read_back(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(spec()))
    rules.append_kills([{"record": "rule_kill", "rule_id": "test-rule"}], tmp_path)
    specs, kills = rules.load(tmp_path)
    assert [s["id"] for s in specs] == ["test-rule"] and kills[0]["rule_id"] == "test-rule"


def test_shadows_never_touch_live_behaviour():
    src = Path(__file__).resolve().parents[1] / "paper"
    for name in ("log.py", "settle.py", "segments.py", "ledger.py"):
        assert "from . import rules" not in (src / name).read_text()
        assert "from .rules" not in (src / name).read_text()


def test_the_report_carries_each_rules_card_and_only_says_eligible():
    rows = [score(i, decided="2026-11-21T00:00:00Z") for i in range(3)]
    rep = build_report([], closing={}, gate_cfg=gate(config()), score_rows=rows,
                       rule_specs=[spec()], rule_kills=[], now=NOW)
    card = rep["shadow_rules"]["test-rule"]
    assert card["status"] == "collecting" and card["n_trades"] == 3
    assert {"promotion", "prereg_at", "kill_limits"} <= set(card)
    assert rep["_new_rule_kills"] == []
