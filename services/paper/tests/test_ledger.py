"""The ledger itself: write-once, first-write-wins, and an untouched candidate line.

Three of the six tests the brief asks for live here, plus the two sizing rules.
Each one is a claim that can only be kept by a file that is append-only, so each
one checks the file itself and not just what the reader returns.
"""
from __future__ import annotations

import json

import pytest
from conftest import build, config, kalshi_row

from paper.ledger import (MAX_BET_FRACTION, MAX_GAME_FRACTION, BANKROLL_UNITS,
                          Ledger, candidate_id, kelly_units, resolved, stake_for)
from paper.settle import settle


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- idempotent logging -------------------------------------------------------

def test_the_same_candidate_logged_twice_is_one_row(ledger_path):
    """Running the logger twice over the same market writes one row, not two."""
    rows, _ = build([kalshi_row()])
    assert len(rows) == 1

    ledger = Ledger(ledger_path)
    first = ledger.append_new(rows)
    second = ledger.append_new(build([kalshi_row()])[0])  # same market, fresh run

    assert len(first) == 1 and second == []
    assert len(ledger.candidates()) == 1
    assert len(_lines(ledger_path)) == 1


def test_a_second_price_for_the_same_bet_is_not_a_new_candidate(ledger_path):
    """A row seen again at a different price is one bet; the first price stands.

    The id hashes what was bet, not when it was seen, so a re-observation cannot
    quietly replace the price the bet was actually made at.
    """
    ledger = Ledger(ledger_path)
    ledger.append_new(build([kalshi_row(yes_ask=0.40)])[0])
    ledger.append_new(build([kalshi_row(yes_ask=0.55)])[0])

    rows = ledger.candidates()
    assert len(rows) == 1
    assert rows[0]["market_price"] == 0.40


def test_the_two_teams_of_one_game_get_different_ids():
    """The regression that put `selection` in the hash.

    A moneyline rung has no player and no line, so Baltimore-yes and
    Cleveland-yes hash identically on (sport, game, market, side, line) alone,
    and the second is swallowed as a duplicate. A quarter of the moneyline
    ledger would disappear without a trace.
    """
    home = candidate_id(sport="nfl", game_id="g1", market="moneyline",
                        selection="BAL", side="yes", line=None)
    away = candidate_id(sport="nfl", game_id="g1", market="moneyline",
                        selection="CLE", side="yes", line=None)
    assert home != away


def test_the_id_does_not_depend_on_when_the_row_was_seen():
    """Same bet, different clock, same id."""
    a = candidate_id(sport="nfl", game_id="g", market="moneyline",
                     selection="BAL", side="yes", line=None)
    b = candidate_id(sport="nfl", game_id="g", market="moneyline",
                     selection="BAL", side="yes", line=None)
    assert a == b


# --- a settled row never changes ---------------------------------------------

def test_settling_never_rewrites_the_candidate_line(ledger_path):
    """The line that says the bet was open stays open on disk forever.

    This is the whole point of the format. The candidate record is the claim made
    at the time it was made, and settlement is a separate record layered on top.
    """
    ledger = Ledger(ledger_path)
    ledger.append_new(build([kalshi_row()])[0])
    before = _lines(ledger_path)

    settle(ledger, [kalshi_row(status="finalized", result="yes")])
    after = _lines(ledger_path)

    assert len(after) == len(before) + 1
    assert after[0] == before[0], "the candidate record must be byte-identical after settling"
    assert after[0]["status"] == "open" and after[0]["outcome"] is None
    assert after[1]["record"] == "settle" and after[1]["outcome"] == "yes"


def test_a_settled_row_reads_back_settled_without_touching_the_file(ledger_path):
    ledger = Ledger(ledger_path)
    ledger.append_new(build([kalshi_row()])[0])
    settle(ledger, [kalshi_row(status="finalized", result="yes")])

    (row,) = resolved(ledger)
    assert row["status"] == "settled" and row["outcome"] == "yes"
    assert row["pnl_per_contract"] == pytest.approx(1.0 - 0.40 - 0.02)


def test_settling_twice_writes_nothing_the_second_time(ledger_path):
    ledger = Ledger(ledger_path)
    ledger.append_new(build([kalshi_row()])[0])
    rows = [kalshi_row(status="finalized", result="yes")]

    assert settle(ledger, rows)["settled"] == 1
    lines_after_first = len(_lines(ledger_path))
    counts = settle(ledger, rows)

    assert counts == {"settled": 0, "void": 0, "already": 1, "unresolved": 0}
    assert len(_lines(ledger_path)) == lines_after_first


def test_resolved_returns_copies_so_no_caller_can_reach_the_disk(ledger_path):
    ledger = Ledger(ledger_path)
    ledger.append_new(build([kalshi_row()])[0])
    (row,) = resolved(ledger)
    row["market_price"] = 0.99
    assert ledger.candidates()[0]["market_price"] == 0.40


# --- sizing -------------------------------------------------------------------

def test_kelly_is_fractional_and_zero_without_an_edge():
    assert kelly_units(0.50, 0.50) == 0.0          # no edge -> no bet
    assert kelly_units(0.40, 0.50) == 0.0          # negative edge -> no bet, not a short
    full = (0.60 - 0.40) / (1 - 0.40)
    assert kelly_units(0.60, 0.40) == pytest.approx(0.25 * full * BANKROLL_UNITS)


def test_a_bet_is_capped_at_one_percent_of_the_bankroll():
    """A huge edge must not produce a huge bet. 0.99 model at a 2c price would
    otherwise be 0.25 * (0.97/0.98) * 1000 = 247 units, 25% of the bankroll."""
    stake = stake_for(0.99, 0.02, game_id="g1")
    assert stake == pytest.approx(MAX_BET_FRACTION * BANKROLL_UNITS)


def test_bets_on_one_game_share_one_two_percent_exposure():
    """The correlated-exposure cap.

    Several bets can ride on one game — the moneyline and the total, or the same
    side quoted again by a later collection — and they are not independent risks,
    so between them they may only spend 2% of the bankroll. The cap is threaded
    through `committed_by_game` rather than recomputed, which is what makes it
    hold for bets that arrive in different runs.

    (Two sides of the SAME market can never both qualify: the model's two
    probabilities sum to 1 and the two asks sum to about 1, so at most one side
    can carry a positive edge. The cap is about different markets on one game and
    about accumulation over time, which is what this exercises.)
    """
    committed = {}
    stakes = []
    for _ in range(4):
        stake = stake_for(0.99, 0.02, game_id="g1", committed_by_game=committed)
        committed["g1"] = committed.get("g1", 0.0) + stake
        stakes.append(stake)

    assert stakes[0] == pytest.approx(MAX_BET_FRACTION * BANKROLL_UNITS)
    assert sum(stakes) == pytest.approx(MAX_GAME_FRACTION * BANKROLL_UNITS, abs=1e-6)
    assert stake_for(0.99, 0.02, game_id="g1", committed_by_game=committed) == 0.0


def test_the_game_cap_counts_what_an_earlier_run_already_committed():
    """The cap holds over the ledger's life, not per run. Ten units are already
    at risk on this game, so the next bet gets the remaining ten."""
    committed = {"g1": 10.0}
    assert stake_for(0.99, 0.02, game_id="g1", committed_by_game=committed) == pytest.approx(10.0)
    assert stake_for(0.99, 0.02, game_id="g1",
                     committed_by_game={"g1": 20.0}) == 0.0


def test_a_game_already_fully_committed_in_the_ledger_gets_no_new_stake(ledger_path):
    """The same rule read back off the file rather than out of a dict."""
    ledger = Ledger(ledger_path)
    made, _ = build([kalshi_row(yes_ask=0.40, yes_bid=0.38)], home_win=0.99)
    ledger.append_new(made)

    committed = ledger.committed_by_game()
    game_id = made[0]["game_id"]
    assert committed[game_id] > 0

    saturated = dict(committed, **{game_id: MAX_GAME_FRACTION * BANKROLL_UNITS})
    second, _ = build([kalshi_row(yes_ask=0.40, yes_bid=0.38)], home_win=0.99,
                      committed=saturated)
    assert second[0]["stake"] == 0.0


def test_a_shadow_row_is_never_staked():
    """A market outside the allow-list can be evaluated, but not bet."""
    cfg = config(segments=[{"segment": "nfl:total", "sport": "nfl", "market": "total"}])
    made, _ = build([kalshi_row()], cfg=cfg)

    assert [r["kind"] for r in made] == ["shadow"]
    assert made[0]["stake"] == 0.0



def test_a_rung_held_on_yes_is_never_also_bought_on_no(ledger_path):
    """The id carries the side, so a later run that prefers NO on the same rung
    would get a fresh id. The ledger must still hold only the first side."""
    ledger = Ledger(ledger_path)
    yes = build([kalshi_row(yes_ask=0.40)])[0]
    assert yes and yes[0]["side"] == "yes"
    flipped = [dict(r, side="no", id=r["id"] + "-no") for r in yes]
    ledger.append_new(yes)
    assert ledger.append_new(flipped) == []
    assert [r["side"] for r in ledger.candidates()] == ["yes"]
