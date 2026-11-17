"""The report, and the gate that says whether anybody may claim an edge.

The gate is the product. Everything else here exists to make sure the gate can
only be passed by evidence, never by a flag.
"""
from __future__ import annotations

import json

import pytest
from conftest import config

from paper import stats
from paper.report import build, main, render
from paper.segments import gate as gate_of


def row(**over) -> dict:
    """A settled ledger row, as `resolved()` would hand it over."""
    base = {
        "record": "candidate", "kind": "candidate", "id": "id0",
        "sport": "nfl", "market": "moneyline",
        "game_id": "g", "ticker": "KXNFLGAME-26OCT18BALCLE-BAL", "selection": "BAL",
        "side": "yes", "line": None,
        "model_p": 0.60, "market_price": 0.50, "fee": 0.02, "edge": 8.0,
        "segment": "nfl:moneyline:0.60-0.70", "stake": 5.0,
        "status": "settled", "outcome": "yes", "pnl_per_contract": 0.48,
        "price_observed_at": "2026-10-17T20:00:00+00:00", "kickoff": "2026-10-18T17:00:00+00:00",
        "expected_end": "2026-10-18T20:00:00+00:00",  # post-fix row: kickoff is the true start
    }
    base.update(over)
    return base


def batch(n: int, *, wins: int | None = None, segment: str = "nfl:moneyline:0.60-0.70") -> list[dict]:
    """`wins` leading rows win. `batch(0)` is empty, which is a real state."""
    wins = n if wins is None else wins
    out = []
    for i in range(n):
        won = i < wins
        out.append(row(id=f"id{i}", game_id=f"g{i}", ticker=f"KXNFLGAME-26OCT18BALCLE-T{i}",
                       segment=segment, outcome="yes" if won else "no",
                       pnl_per_contract=0.48 if won else -0.52))
    return out


GATE = gate_of(config())


# --- the three conditions ------------------------------------------------------

def test_an_empty_ledger_produces_no_segments_to_gate():
    """n=0 is a real answer with a real reason, not a missing key."""
    report = build(batch(0), closing={}, gate_cfg=GATE)
    assert report["segments"] == {}
    assert report["totals"]["segments"] == 0
    assert report["totals"]["segments_qualifying"] == 0


def test_a_segment_with_fewer_than_three_hundred_settled_bets_never_qualifies():
    for n in (1, 99, 299):
        report = build(batch(n), closing={}, gate_cfg=GATE)
        s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
        assert s["qualifies_for_edge_claim"] is False
        assert any("need 300" in r for r in s["disqualifying"]), n


def test_three_hundred_settled_bets_that_always_win_qualify():
    """The gate can be passed. A report that cannot say yes is not a gate, it is
    a wall — this is the shape of evidence that would earn the claim."""
    report = build(batch(300), closing={}, gate_cfg=GATE)
    s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["qualifies_for_edge_claim"] is True
    assert s["disqualifying"] == []


def test_three_hundred_bets_that_always_lose_do_not_qualify():
    """The gate must require the interval to sit ABOVE zero, not merely to avoid
    it. Losing all 300 bets is the tightest, most confident interval available —
    entirely below zero — and a gate that only asked "does this exclude zero"
    would hand it a pass and print "yes: has an edge" about a segment that lost
    money every single time."""
    report = build(batch(300, wins=0), closing={}, gate_cfg=GATE)
    s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]

    assert s["n_settled"] == 300
    assert s["pnl"] < 0 and s["roi"] < 0
    assert s["qualifies_for_edge_claim"] is False
    assert any("below zero" in r for r in s["disqualifying"])
    # And the interval really does exclude zero — that is the trap.
    assert stats.excludes_zero(s["pnl_ci95"]) is True
    assert stats.is_profitable(s["pnl_ci95"]) is False


def test_a_marginal_segment_is_reported_as_including_zero():
    """Near coin-flip pnl must not be passed off as an edge."""
    report = build(batch(300, wins=149), closing={}, gate_cfg=GATE)
    s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["qualifies_for_edge_claim"] is False
    assert stats.excludes_zero(s["pnl_ci95"]) is False


def test_a_losing_segment_with_a_positive_interval_still_needs_its_numbers_to_agree():
    """n >= 300 is not enough on its own: the interval has to exclude zero."""
    report = build(batch(300, wins=100), closing={}, gate_cfg=GATE)
    s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["n_settled"] == 300
    assert s["qualifies_for_edge_claim"] is False


def test_the_bootstrap_interval_is_reproducible():
    """It gates on "does this interval exclude zero", so it cannot be a coin flip
    that changes between two runs of the same ledger."""
    rows = batch(300, wins=160)
    a = build(rows, closing={}, gate_cfg=GATE)
    b = build(rows, closing={}, gate_cfg=GATE)
    key = "nfl:moneyline:0.60-0.70 :: unknown"
    assert a["segments"][key]["pnl_ci95"] == b["segments"][key]["pnl_ci95"]


def test_calibration_must_not_be_widening():
    """A segment whose gap grows over time fails even when it has the samples and
    a clean interval."""
    rows = batch(300)
    # First half: model says 0.60 and it happens. Second half: model says 0.60
    # and it stops happening. The gap goes 0 -> 40 points.
    for r in rows[150:]:
        r["outcome"] = "no"
        r["pnl_per_contract"] = -0.52
    report = build(rows, closing={}, gate_cfg=GATE)
    s = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    trend = s["calibration_trend"]
    assert trend["measurable"] is True and trend["widening"] is True
    assert s["qualifies_for_edge_claim"] is False
    assert any("widening" in r for r in s["disqualifying"])


def test_a_calibration_trend_on_too_few_bets_says_so_rather_than_guessing():
    """'Not widening' on seven bets is indistinguishable from 'we have not looked'."""
    report = build(batch(10), closing={}, gate_cfg=GATE)
    trend = report["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]["calibration_trend"]
    assert trend["measurable"] is False and trend["widening"] is None


def test_a_shadow_segment_can_never_qualify():
    """Shadows are logged so they can be evaluated before they are bet on. Sizing
    them would defeat the point."""
    rows = [row(id=f"s{i}", kind="shadow", stake=0.0, game_id=f"g{i}",
                ticker=f"T{i}", segment="cfb:total:0.60-0.70") for i in range(400)]
    for r in rows:
        r["pnl_per_contract"] = 0.5
    report = build(rows, closing={}, gate_cfg=GATE)
    s = report["segments"]["cfb:total:0.60-0.70 :: unknown"]
    assert s["qualifies_for_edge_claim"] is False
    assert any("shadow" in r for r in s["disqualifying"])


# --- the numbers ---------------------------------------------------------------

def test_realised_edge_is_the_hit_rate_minus_what_the_model_claimed():
    s = build(batch(100, wins=70), closing={}, gate_cfg=GATE)["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["hit_rate"] == pytest.approx(0.70)
    assert s["realised_edge_points"] == pytest.approx((0.70 - 0.60) * 100)


def test_pnl_roi_and_drawdown_agree_with_the_rows():
    s = build(batch(10, wins=7), closing={}, gate_cfg=GATE)["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["pnl"] == pytest.approx(7 * 0.48 + 3 * -0.52)
    assert s["staked"] == pytest.approx(50.0)
    assert s["roi"] == pytest.approx(s["pnl"] / 50.0)
    assert s["max_drawdown"] == pytest.approx(stats.max_drawdown([0.48] * 7 + [-0.52] * 3))


def test_max_drawdown_never_reports_a_gain():
    assert stats.max_drawdown([1, 1, 1]) == 0.0
    assert stats.max_drawdown([-1, -1]) == pytest.approx(2.0)


def test_closing_line_value_is_positive_when_we_beat_the_close():
    """We logged 50c; the last price before kickoff was 45c — we paid MORE than
    the market ended up charging, which is negative CLV."""
    rows = batch(5)
    for r in rows:
        r["ticker"] = "KXNFLGAME-26OCT18BALCLE-BAL"
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {"_seen_at": 1, "_kickoff": 2, "_price": 0.45}}
    s = build(rows, closing=closing, gate_cfg=GATE)["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["mean_clv_cents"] == pytest.approx(-5.0)
    assert s["n_clv"] == 5


def test_closing_line_value_is_positive_when_the_market_moved_against_our_entry():
    """We logged 50c and it closed at 55c: we got the bet done before the market
    repriced it. That is the part of an edge which should persist."""
    rows = batch(5)
    for r in rows:
        r["ticker"] = "KXNFLGAME-26OCT18BALCLE-BAL"
    closing = {"KXNFLGAME-26OCT18BALCLE-BAL": {"_seen_at": 1, "_kickoff": 2, "_price": 0.55}}
    s = build(rows, closing=closing, gate_cfg=GATE)["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["mean_clv_cents"] == pytest.approx(5.0)


def test_an_unobserved_ticker_reports_no_clv_rather_than_zero():
    """No closing line is missing data, not a CLV of exactly zero."""
    s = build(batch(5), closing={}, gate_cfg=GATE)["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]
    assert s["mean_clv_cents"] is None and s["n_clv"] == 0


# --- output --------------------------------------------------------------------

def test_an_empty_ledger_prints_no_segments_and_says_so(tmp_path, capsys):
    report = build([], closing={}, gate_cfg=GATE)
    text = render(report)
    assert "no segments" in text
    assert "0/0 segment-groups qualify" in text


def test_the_report_is_written_as_json_and_matches_what_was_printed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in batch(300)))
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps(config()))
    out = tmp_path / "report.json"

    assert main(["--ledger", str(ledger), "--segments", str(segments), "--out", str(out)]) == 0
    written = json.loads(out.read_text())
    assert written["schema_version"] == 1
    assert written["gate"]["min_settled"] == 300
    assert written["segments"]["nfl:moneyline:0.60-0.70 :: unknown"]["qualifies_for_edge_claim"] is True


def test_legacy_total_rows_without_a_model_basis_are_excluded_from_the_report():
    """Total rows logged before the per-line fix carry the book line's over_prob for
    every rung. They stay in the append-only ledger but must not count as evidence."""
    legacy = row(market="total", segment="nfl:total:0.50-0.60", id="old", model_basis=None)
    sound = row(market="total", segment="nfl:total:0.50-0.60", id="new",
                model_basis="normal(predicted_total, total_sigma) at the rung line")
    report = build([legacy, sound], closing={}, gate_cfg=GATE)
    assert report["totals"]["excluded_legacy_total_rows"] == 1
    assert report["totals"]["n_logged"] == 1
    assert sum(v["n_logged"] for v in report["segments"].values()) == 1
