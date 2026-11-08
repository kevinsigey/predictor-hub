"""Near-kickoff closing capture: fetch parsing, window, idempotency, and how the
report treats the observations (close must be after logging and before kickoff)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from paper import closing, stats
from paper.report import build
from paper.segments import gate as gate_of

from conftest import config
from test_clv import ledger_row

NOW = datetime(2026, 10, 18, 16, 30, tzinfo=timezone.utc)
KICK = "2026-10-18T17:00:00+00:00"
T = "KXNFLGAME-26OCT18BALCLE-BAL"


def market_body(yes_ask="0.47", no_ask="0.55", yes_bid="0.45", no_bid="0.53"):
    return json.dumps({"market": {"yes_ask_dollars": yes_ask, "yes_bid_dollars": yes_bid,
                                  "no_ask_dollars": no_ask, "no_bid_dollars": no_bid}})


def write_ledger(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_parse_market_uses_buyable_price_rules():
    assert closing.parse_market(market_body()) == {"yes_price": 0.47, "no_price": 0.55}
    # a dead side (ask 1.00) is not buyable; the live side survives
    assert closing.parse_market(market_body(yes_ask="1.0000", no_ask="0.55")) == \
        {"yes_price": None, "no_price": 0.55}
    assert closing.parse_market("not json") is None
    assert closing.parse_market(market_body(yes_ask="1.0", no_ask="0.0")) is None


def test_fetch_retries_429_and_5xx_then_gives_up(monkeypatch):
    calls = []
    script = iter([(429, ""), (503, ""), (200, market_body())])
    sleeps = []
    out = closing.fetch(T, get=lambda url: (calls.append(url), next(script))[1], sleep=sleeps.append)
    assert out == {"yes_price": 0.47, "no_price": 0.55}
    assert calls[0].endswith("/markets/" + T) and sleeps == [1, 2]
    assert closing.fetch(T, get=lambda u: (404, ""), sleep=sleeps.append) is None
    assert closing.fetch(T, get=lambda u: (500, ""), sleep=lambda s: None) is None


def test_window_selection(tmp_path):
    rows = [
        ledger_row(id="a", ticker="IN", kickoff=KICK),
        ledger_row(id="b", ticker="STARTED", kickoff="2026-10-18T16:00:00+00:00"),
        ledger_row(id="c", ticker="LATER", kickoff="2026-10-18T17:20:00+00:00"),
        ledger_row(id="d", ticker="SETTLED", kickoff=KICK, status="settled"),
        ledger_row(id="e", ticker="NOKICK", kickoff=None),
    ]
    assert set(closing.due_tickers(rows, NOW)) == {"IN"}


def test_run_writes_one_observation_per_ticker_and_is_idempotent(tmp_path):
    led, out = tmp_path / "l.jsonl", tmp_path / "closing.jsonl"
    write_ledger(led, [ledger_row(id="a", ticker=T, kickoff=KICK),
                       ledger_row(id="b", ticker=T, kickoff=KICK, side="no"),
                       ledger_row(id="c", ticker="BAD", kickoff=KICK)])

    def get(url):
        if url.endswith("BAD"):
            raise RuntimeError("boom")  # one ticker failing must not stop the rest
        return 200, market_body()

    assert closing.run(str(led), str(out), get=get, now=NOW, sleep=lambda s: None) == 1
    assert closing.run(str(led), str(out), get=get, now=NOW, sleep=lambda s: None) == 0
    obs = [json.loads(l) for l in out.read_text().splitlines()]
    assert obs == [{"ticker": T, "observed_at": "2026-10-18T16:30:00+00:00",
                    "yes_price": 0.47, "no_price": 0.55, "source": "kalshi-public"}]


def test_nothing_to_do_exits_clean_and_writes_nothing(tmp_path):
    led, out = tmp_path / "l.jsonl", tmp_path / "closing.jsonl"
    write_ledger(led, [])
    assert closing.run(str(led), str(out), get=lambda u: (200, ""), now=NOW) == 0
    assert not out.exists()


# --- how the report uses the observations --------------------------------------

def clv_for(obs_at, row=None):
    row = row or ledger_row(kickoff=KICK, side="yes", market_price=0.40)
    obs = [{"ticker": T, "observed_at": obs_at, "yes_price": 0.47, "no_price": 0.55}]
    cl = stats.closing_prices(closing.observation_rows(obs, [row]))
    return stats.clv_points(row, cl)


def test_close_after_logging_and_before_kickoff_gives_clv():
    assert clv_for("2026-10-18T16:30:00+00:00") == pytest.approx(7.0)


def test_close_before_logging_is_none_not_zero():
    # row logged 2026-10-17T20:00; an observation before that is not a close
    assert clv_for("2026-10-17T10:00:00+00:00") is None


def test_close_at_or_after_kickoff_is_ignored():
    assert clv_for(KICK) is None
    assert clv_for("2026-10-18T17:05:00+00:00") is None


def test_latest_valid_observation_wins_over_site_snapshot():
    row = ledger_row(kickoff=KICK, market_price=0.40)
    site = [{"ticker": T, "_seen_at": datetime(2026, 10, 18, 9, tzinfo=timezone.utc),
             "_kickoff": datetime(2026, 10, 18, 17, tzinfo=timezone.utc), "_price": 0.42, "_no_price": 0.60}]
    obs = closing.observation_rows(
        [{"ticker": T, "observed_at": "2026-10-18T16:45:00+00:00", "yes_price": 0.50, "no_price": 0.52}], [row])
    assert stats.clv_points(row, stats.closing_prices(site + obs)) == pytest.approx(10.0)


# --- bootstrap clusters by game --------------------------------------------------

def test_clv_bootstrap_resamples_games_not_bets():
    # two games; one has 20 identical rungs. Per-bet resampling would give a tight
    # interval near the pooled mean; the game cluster interval must reach both game means.
    pairs = [("g1", 10.0)] * 20 + [("g2", 0.0)] * 2
    s = stats.clv_summary(pairs)
    lo, hi = s["clv_ci95"]
    assert lo == pytest.approx(0.0) and hi == pytest.approx(10.0)
    # deterministic
    assert stats.clv_summary(pairs) == s


def test_bets_to_resolve():
    assert stats.clv_summary([("g", 1.0)] * 9)["clv_bets_to_resolve"] is None
    pairs = [(f"g{i}", 10.0 if i % 2 else -10.0) for i in range(20)]  # sd ~10.26
    s = stats.clv_summary(pairs)
    assert s["clv_bets_to_resolve"] == 385
    assert stats.clv_summary([("g%d" % i, 0.0) for i in range(12)])["clv_bets_to_resolve"] == 0


def test_report_carries_new_clv_fields_per_sport_and_total():
    rows = [ledger_row(id=f"i{i}", game_id=f"g{i}", kickoff=KICK, market_price=0.40) for i in range(12)]
    obs = closing.observation_rows(
        [{"ticker": T, "observed_at": "2026-10-18T16:30:00+00:00", "yes_price": 0.50, "no_price": 0.5}], rows)
    rep = build(rows, closing=stats.closing_prices(obs), gate_cfg=gate_of(config()))
    for scope in (rep["totals"], rep["per_sport"]["nfl"]):
        assert scope["n_clv"] == 12 and scope["mean_clv_cents"] == pytest.approx(10.0)
        assert scope["share_clv_positive"] == 1.0
        assert scope["clv_ci95"] == [10.0, 10.0]
        assert scope["clv_bets_to_resolve"] == 0
