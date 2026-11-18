"""NFL totals: the model probability must be for the rung's own line.

The ladder has one rung per threshold ("over 41.5", "over 47.5", "over 60.5"...).
The first wiring gave every rung the snapshot's single book-line over_prob, which
priced "over 60.5" as if it were "over the book line" and logged 25-43 point fake
edges that lost money. The probability now comes from the model's predicted total
and sigma (Normal), evaluated at each rung's line.
"""
from __future__ import annotations

import math

import pytest
from conftest import build, config, kalshi_row, nfl_snapshot

TOTAL_CFG = config(segments=[{"segment": "nfl:total", "sport": "nfl", "market": "total"}])


def total_row(line: float, **over) -> dict:
    return kalshi_row(
        series="KXNFLTOTAL", stat="total", line=line,
        event_ticker="KXNFLTOTAL-26OCT18BALCLE",
        ticker=f"KXNFLTOTAL-26OCT18BALCLE-{int(line)}",
        **over,
    )


def p_over(line: float, mu: float = 47.0, sigma: float = 12.0) -> float:
    return 0.5 * (1.0 - math.erf((line - mu) / sigma / math.sqrt(2.0)))


def test_the_probability_is_for_the_rungs_own_line():
    # "over 41.5" is likelier than "over the model's 47.0 total"; priced at 40c it is a bet.
    made, _ = build([total_row(41.5, yes_ask=0.40, yes_bid=0.38)], cfg=TOTAL_CFG)
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["model_p"] == pytest.approx(p_over(41.5), abs=1e-5)
    assert made[0]["model_p"] > 0.6
    assert "predicted_total" in made[0]["model_basis"]


def test_a_far_rung_is_not_given_the_book_lines_probability():
    # The old bug: "over 60.5" got ~0.5 and looked like a huge edge at a 26c price.
    made, skipped = build([total_row(60.5, yes_ask=0.26, yes_bid=0.24, no_bid=0.74, no_ask=0.76)], cfg=TOTAL_CFG)
    assert p_over(60.5) < 0.2   # the model agrees with the market that this is unlikely
    # The guard, in both-sides terms (the logger has evaluated NO since
    # 2026-11-17): the rung never logs as a YES carrying the book line's
    # probability. What logs is the NO side, priced off the rung's OWN
    # probability -- the market sells "not over 60.5" at 76c while the
    # rung's own figure says it is worth ~87c.
    assert [r["side"] for r in made] == ["no"]
    assert made[0]["model_p"] == pytest.approx(p_over(60.5), abs=1e-5)


def test_missing_total_model_is_skipped_not_guessed():
    snap = nfl_snapshot(predicted_total=None, total_sigma=None)
    made, skipped = build([total_row(41.5, yes_ask=0.40, yes_bid=0.38)], cfg=TOTAL_CFG, snapshot=snap)
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_non_positive_sigma_is_refused():
    snap = nfl_snapshot(total_sigma=0.0)
    made, skipped = build([total_row(41.5, yes_ask=0.40, yes_bid=0.38)], cfg=TOTAL_CFG, snapshot=snap)
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_rung_without_a_line_is_refused():
    row = total_row(41.5, yes_ask=0.40, yes_bid=0.38)
    row["line"] = None
    made, skipped = build([row], cfg=TOTAL_CFG)
    assert made == []
    assert skipped.get("no_model_probability") == 1
