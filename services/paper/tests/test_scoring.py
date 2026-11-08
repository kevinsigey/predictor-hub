"""Q1 scoring rows, the scoring block, and the sport-level verdict.

Score rows are a separate, no-stake population: they must never be counted as
bets, candidates or P&L, and the earliest observation per game+market is the one
that stands.
"""
from __future__ import annotations

import json
import random

import pytest
from conftest import build, config, kalshi_row

from paper import report, stats
from paper.ledger import Ledger, resolved, resolved_scores
from paper.log import candidate_rows, score_records
from paper.segments import gate as gate_of, sport_gate
from paper.settle import settle

import conftest

EARLY = "2026-10-17T20:00:00+00:00"
LATER = "2026-10-17T22:00:00+00:00"
AFTER = "2026-10-18T18:00:00+00:00"


def observe(rows, **kw):
    obs: list[dict] = []
    made, _ = _run(rows, obs, **kw)
    return made, obs


def _run(rows, obs, home_win=0.60):
    snap = conftest.nfl_snapshot(home_win=home_win)
    from paper import sources
    return candidate_rows(rows, {"nfl": sources.snapshot_games(snap, "nfl")}, config(), scores=obs)


def total_rung(line, ask, **kw):
    return kalshi_row(series="KXNFLTOTAL", stat="total", event_ticker="KXNFLTOTAL-26OCT18BALCLE",
                      ticker=f"KXNFLTOTAL-26OCT18BALCLE-{int(line)}", line=line,
                      yes_bid=ask - 0.02, yes_ask=ask, **kw)


# --- logging -------------------------------------------------------------------

def test_moneyline_scores_only_the_home_side_and_even_a_dead_edge_is_scored():
    home = kalshi_row()  # BAL (home) at 40c; model 0.60
    away = kalshi_row(ticker="KXNFLGAME-26OCT18BALCLE-CLE", yes_ask=0.55, yes_bid=0.5)
    # A price the model agrees with: no candidate, but still a score row.
    _, obs = observe([home, away], home_win=0.41)
    (rec,) = score_records(obs)
    assert rec["record"] == "score" and rec["stake"] == 0.0
    assert rec["ticker"].endswith("-BAL") and rec["side"] == "yes"
    assert rec["model_p"] == 0.41 and rec["market_price"] == 0.40
    assert rec["market_mid"] == pytest.approx(0.39)
    assert rec["logged_at"] == rec["price_observed_at"] == EARLY
    assert rec["kickoff"] and "model_fingerprint" in rec


def test_total_picks_the_rung_closest_to_fifty_cents():
    rows = [total_rung(41.5, 0.80), total_rung(44.5, 0.52), total_rung(50.5, 0.20)]
    _, obs = observe(rows)
    (rec,) = score_records(obs)
    assert rec["ticker"].endswith("-44") and rec["line"] == 44.5


def test_earliest_observation_wins_and_the_ledger_never_overwrites(ledger_path):
    early = kalshi_row(snapshot_ts=EARLY)
    late = kalshi_row(snapshot_ts=LATER, yes_ask=0.45, yes_bid=0.43)
    _, obs = observe([late, early])
    (rec,) = score_records(obs)
    assert rec["market_price"] == 0.40 and rec["logged_at"] == EARLY
    ledger = Ledger(ledger_path)
    ledger.append_new([rec])
    _, obs2 = observe([late])
    ledger.append_new(score_records(obs2))
    (kept,) = ledger.scores()
    assert kept["market_price"] == 0.40


def test_a_post_kickoff_price_is_never_scored():
    _, obs = observe([kalshi_row(snapshot_ts=AFTER)])
    assert obs == []


def test_a_model_published_after_the_price_is_never_scored():
    snap = conftest.nfl_snapshot(generated_at="2026-10-17T21:00:00+00:00")
    from paper import sources
    obs: list[dict] = []
    candidate_rows([kalshi_row()], {"nfl": sources.snapshot_games(snap, "nfl")}, config(), scores=obs)
    assert obs == []


def test_f1_scores_one_row_per_driver_ticker_only_for_allow_listed_markets():
    def o(market, ticker, allow=True):
        import datetime as dt
        t = dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc)
        return {"sport": "f1", "market": market, "game_id": "race1", "ticker": ticker, "selection": ticker,
                "line": None, "model_p": 0.2, "price": 0.25, "seen_at": t, "yes_bid": 0.2,
                "kickoff": t, "home": None, "fingerprint": None, "allow_listed": allow}
    recs = score_records([o("race_win", "A"), o("race_win", "B"), o("race_win", "A"),
                          o("fastest_lap", "C", allow=False)])
    assert sorted(r["ticker"] for r in recs) == ["A", "B"]


def test_score_rows_never_enter_bets_candidates_or_pnl(ledger_path):
    ledger = Ledger(ledger_path)
    made, obs = observe([kalshi_row()])
    ledger.append_new(made + score_records(obs))
    settle(ledger, [kalshi_row(status="finalized", result="yes")])
    assert len(ledger.candidates()) == len(made) == 1
    assert len(resolved(ledger)) == 1
    rep = report.build(resolved(ledger), closing={}, gate_cfg=gate_of(config()),
                       score_rows=resolved_scores(ledger))
    assert rep["totals"]["n_logged"] == 1
    assert rep["verdicts"]["pooled"]["all_time"]["n_settled_bets"] == 1
    # And with the score row removed, the pre-existing blocks are identical.
    bare = report.build(resolved(ledger), closing={}, gate_cfg=gate_of(config()))
    for key in ("totals", "per_sport", "per_segment", "segments"):
        assert rep[key] == bare[key]


# --- settlement ------------------------------------------------------------------

def test_scores_settle_from_kalshi_and_a_void_is_excluded(ledger_path):
    ledger = Ledger(ledger_path)
    _, obs = observe([kalshi_row()])
    ledger.append_new(score_records(obs))
    counts = settle(ledger, [kalshi_row(status="finalized", result="yes")])
    assert counts["scores_settled"] == 1
    (row,) = resolved_scores(ledger)
    assert row["outcome"] == "yes" and row["status"] == "settled" and "pnl_per_contract" not in row
    assert settle(ledger, [kalshi_row(status="finalized", result="yes")])["scores_settled"] == 0

    void_ledger = Ledger(ledger_path.with_name("v.jsonl"))
    void_ledger.append_new(score_records(obs))
    settle(void_ledger, [kalshi_row(status="finalized", result="")])
    (v,) = resolved_scores(void_ledger)
    assert v["outcome"] == "void"
    assert report.score_points([v], []) == []


# --- the scoring block -----------------------------------------------------------

def sc(i, model_p, price, y, game=None, **kw):
    return {"record": "score", "id": f"s{i}", "sport": "nba", "market": "moneyline",
            "game_id": game or f"g{i}", "ticker": f"T{i}", "model_p": model_p, "market_price": price,
            "outcome": "yes" if y else "no", "status": "settled", "logged_at": f"2026-10-17T{i % 24:02d}:00:00+00:00",
            "model_fingerprint": "fp", **kw}


def truth_sample(n, seed=1):
    """Outcomes drawn from the market's own probabilities: the market is calibrated."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.uniform(0.2, 0.8)
        out.append((p, 1 if rng.random() < p else 0))
    return out


def test_a_perfect_market_sample_gives_a_difference_near_zero():
    # Model == market exactly: the paired difference is exactly zero.
    pts = report.score_points([sc(i, p, p, y) for i, (p, y) in enumerate(truth_sample(300))], [])
    s = stats.score_summary(pts)
    assert s["paired_logloss_diff"] == pytest.approx(0.0, abs=1e-9)
    assert s["log_loss_model"] == s["log_loss_market"]


def test_a_noisy_model_against_a_calibrated_market_scores_negative():
    rng = random.Random(7)
    rows = []
    for i, (p, y) in enumerate(truth_sample(400, seed=3)):
        noisy = min(0.99, max(0.01, p + rng.uniform(-0.3, 0.3)))
        rows.append(sc(i, noisy, p, y))
    s = stats.score_summary(report.score_points(rows, []))
    assert s["paired_logloss_diff"] < 0
    assert s["paired_logloss_diff_ci95"][1] < 0 and s["p_model_better"] < 0.01


def test_probabilities_are_clipped_so_a_certain_miss_has_finite_loss():
    s = stats.score_summary(report.score_points([sc(0, 0.0, 0.5, 1)], []))
    assert s["log_loss_model"] == pytest.approx(-__import__("math").log(0.01), abs=1e-5)


def test_two_rows_of_one_game_do_not_double_count():
    one = [sc(0, 0.7, 0.5, 1, game="G")]
    two = one + [sc(1, 0.7, 0.5, 1, game="G")]
    other = [sc(2, 0.4, 0.5, 0, game="H")]
    a = stats.score_summary(report.score_points(one + other, []))
    b = stats.score_summary(report.score_points(two + other, []))
    assert b["n_games"] == a["n_games"] == 2 and b["n_rows"] == 3
    assert b["paired_logloss_diff"] == a["paired_logloss_diff"]
    assert b["paired_logloss_diff_ci95"] == a["paired_logloss_diff_ci95"]


def cand(i, game, ticker, model_p=0.6, price=0.5, y=True, kind="candidate", logged="2026-10-17T10:00:00+00:00"):
    return {"record": "candidate", "kind": kind, "id": f"c{i}", "sport": "nba", "market": "moneyline",
            "game_id": game, "ticker": ticker, "model_p": model_p, "market_price": price, "logged_at": logged,
            "status": "settled", "outcome": "yes" if y else "no", "pnl_per_contract": 0.4 if y else -0.5,
            "model_fingerprint": "fp", "stake": 5.0 if kind == "candidate" else 0.0}


def test_backfill_fills_only_uncovered_games_and_dedupes_earliest_wins():
    scores = [sc(0, 0.6, 0.5, 1, game="covered")]
    others = [
        cand(1, "covered", "TX"),                                   # game+market has a score row: skipped
        cand(2, "open", "T1", model_p=0.9, logged="2026-10-17T12:00:00+00:00"),
        cand(3, "open", "T1", model_p=0.6, logged="2026-10-17T09:00:00+00:00", kind="shadow"),  # earliest
        cand(4, "open", "T2"),                                      # another rung of the same game
    ]
    pts = report.score_points(scores, others)
    assert [p["source"] for p in pts] == ["score", "backfill", "backfill"]
    assert sorted(p["model_p"] for p in pts if p["source"] == "backfill") == [0.6, 0.6]
    s = stats.score_summary(pts)
    assert (s["n_from_score_rows"], s["n_from_backfill"], s["n_games"]) == (1, 2, 2)


def test_games_to_resolve_is_null_under_ten_games_and_counts_down_after():
    rng = random.Random(5)
    rows = [sc(i, 0.5, 0.5, rng.random() < 0.5) for i in range(9)]
    assert stats.score_summary(report.score_points(rows, []))["games_to_resolve"] is None
    big = [sc(i, 0.6, 0.5, rng.random() < 0.55) for i in range(40)]
    need = stats.score_summary(report.score_points(big, []))["games_to_resolve"]
    assert isinstance(need, int) and need > 0


# --- the verdict ------------------------------------------------------------------

CFG = sport_gate({})


def verdict_for(rows, scores=()):
    rep = report.build(rows, closing={}, gate_cfg=gate_of(config()), score_rows=list(scores),
                       sport_gate_cfg=CFG)
    return rep["verdicts"]["per_sport"]["nba"]["all_time"]


def bets(n, win_rate, *, price=0.5, model_p=0.6, seed=2):
    rng = random.Random(seed)
    return [cand(i, f"g{i}", f"T{i}", model_p=model_p, price=price, y=rng.random() < win_rate)
            for i in range(n)]


def test_config_is_pre_stated():
    assert CFG["min_settled_bets"] == 100
    assert CFG["min_events"] == {"default": 50, "f1": 20, "pl": 60}
    assert json.load(open("../../data/paper/segments.json"))["sport_gate"]["min_events"] == CFG["min_events"]


def test_below_the_floors_is_not_yet_whatever_the_point_estimate():
    v = verdict_for(bets(30, 0.9))
    assert v["status"] == "not_yet" and v["n_settled_bets"] == 30


def test_a_miscalibrated_model_that_loses_is_against():
    rows = bets(200, 0.2)           # claims 0.6, wins 20%
    for r in rows:
        r["pnl_per_contract"] = 0.48 if r["outcome"] == "yes" else -0.52
    scores = [sc(i, 0.6, 0.5, r["outcome"] == "yes", game=r["game_id"]) for i, r in enumerate(rows)]
    v = verdict_for(rows, scores)
    assert v["status"] == "against"
    assert v["pnl_per_contract_ci95"][1] < 0 and v["q1_paired_logloss_diff"] < 0
    assert v["needed_bets_at_observed_effect"] is None


def test_leaning_when_favourable_but_the_interval_includes_zero_then_proven_with_more_evidence():
    def mk(n, rate):
        rows = bets(n, rate, seed=9)
        for r in rows:
            r["pnl_per_contract"] = 0.5 if r["outcome"] == "yes" else -0.5
        return rows
    small = verdict_for(mk(120, 0.53))
    assert small["status"] == "leaning" and small["pnl_per_contract"] > 0
    assert small["pnl_per_contract_ci95"][0] <= 0
    assert small["needed_bets_at_observed_effect"] > 0

    big_rows = mk(3000, 0.58)
    for i, r in enumerate(big_rows):   # same hit rate in both halves: calibration not widening
        r["outcome"] = "yes" if i % 50 < 29 else "no"
        r["pnl_per_contract"] = 0.5 if r["outcome"] == "yes" else -0.5
    scores = [sc(i, 0.58, 0.5, r["outcome"] == "yes", game=r["game_id"]) for i, r in enumerate(big_rows)]
    big = verdict_for(big_rows, scores)
    assert big["status"] == "proven", big
    assert big["needed_bets_at_observed_effect"] == 0


def test_shadow_rows_are_not_bets_for_the_verdict():
    rows = bets(150, 1.0)
    for r in rows:
        r["kind"] = "shadow"
    assert verdict_for(rows)["n_settled_bets"] == 0


def test_current_model_only_drops_a_superseded_fingerprint():
    old = bets(120, 0.9, seed=1)
    new = bets(60, 0.9, seed=2)
    for i, r in enumerate(new):
        r.update(model_fingerprint="fp2", game_id=f"n{i}", id=f"n{i}", logged_at="2026-10-30T10:00:00+00:00")
    scores = [sc(1000, 0.6, 0.5, 1, model_fingerprint="fp"), sc(1001, 0.6, 0.5, 1, model_fingerprint="fp2",
              logged_at="2026-10-30T11:00:00+00:00")]
    rep = report.build(old + new, closing={}, gate_cfg=gate_of(config()), sport_gate_cfg=CFG,
                       score_rows=scores)
    assert rep["verdicts"]["per_sport"]["nba"]["current_model_only"]["q1_n_games"] == 61
    v = rep["verdicts"]["per_sport"]["nba"]
    assert v["all_time"]["n_settled_bets"] == 180
    assert v["current_model_only"]["n_settled_bets"] == 60
    assert v["current_model_only"]["status"] == "not_yet"
    assert rep["verdicts"]["pooled"]["current_model_only"]["n_settled_bets"] == 60


def test_segment_gate_floors_are_unchanged():
    assert gate_of(config())["min_settled"] == 300 and gate_of(config())["min_settled_games"] == 100
