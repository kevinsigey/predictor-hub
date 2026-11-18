"""The three-leg promotion test a shadow rule must pass to be called eligible.

  1. n >= 150 settled trades;
  2. game-clustered low95 of net cents per contract > 0;
  3. paired low95 > 0 against the incumbent (the live model's rows) on the
     rule's own games, the incumbent scoring 0 on a game it did not bet.

"Eligible" is all the report ever says: promotion to live needs Kevin.
"""
from __future__ import annotations

from paper.report import PROMOTION_MIN_N, promotion_test


def _t(game, pnl):
    return {"game_id": game, "pnl_per_contract": pnl}


def _rule(n_games, per_game, pnl):
    return [_t(f"g{g}", pnl(g, k)) for g in range(n_games) for k in range(per_game)]


def test_the_floor_is_150():
    assert PROMOTION_MIN_N == 150


def test_a_strong_rule_against_a_flat_incumbent_is_eligible():
    rule = _rule(100, 2, lambda g, k: 0.30 if g % 3 else -0.20)
    out = promotion_test(rule, [_t(f"g{g}", 0.0) for g in range(100)])
    assert out["n"] == 200 and out["n_games"] == 100
    assert out["legs"] == {"n": True, "net_low95": True, "paired_low95": True}
    assert out["eligible"] is True
    assert out["net_cents_per_contract"] > 0 and out["net_ci95"][0] > 0


def test_too_few_trades_fails_leg_one_alone():
    rule = _rule(70, 2, lambda g, k: 0.30)
    out = promotion_test(rule, [])
    assert out["legs"]["n"] is False and out["legs"]["net_low95"] is True
    assert out["eligible"] is False


def test_many_rungs_on_two_games_cannot_manufacture_a_tight_interval():
    rule = _rule(2, 100, lambda g, k: 0.40 if g == 0 else -0.10)
    out = promotion_test(rule, [])
    assert out["n"] == 200 and out["n_games"] == 2
    assert out["net_ci95"][0] < 0 and out["legs"]["net_low95"] is False


def test_a_profitable_rule_that_trails_the_incumbent_fails_the_paired_leg():
    rule = _rule(100, 2, lambda g, k: 0.10)
    incumbent = [_t(f"g{g}", 0.25) for g in range(100)]
    out = promotion_test(rule, incumbent)
    assert out["legs"]["net_low95"] is True and out["legs"]["paired_low95"] is False
    assert out["paired_cents_per_contract"] < 0


def test_the_incumbent_scores_zero_on_a_game_it_did_not_bet():
    rule = _rule(100, 2, lambda g, k: 0.10)
    out = promotion_test(rule, [_t("other", 5.0)])
    assert out["paired_cents_per_contract"] == out["net_cents_per_contract"]
