"""Kalshi's `occurrence` is the expected END (about 3h after the start), so the
start comes from the model site's own schedule. Real-shaped rows throughout:
NFL 1pm ET (17:00Z) with Kalshi occurrence 20:00Z, NBA tip_off '...Z', PL commence_time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import build, config, kalshi_row, nfl_snapshot

from paper import closing, report, sources, stats
from paper.segments import gate as gate_of

START = datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)
END = "2026-10-18T20:00:00+00:00"


# --- sources: the site's own start ---------------------------------------------

def test_game_starts_per_sport_from_the_sites_own_schedule():
    assert sources.game_starts(nfl_snapshot(), "nfl") == {"2026_06_BAL_CLE": START}  # naive gameday is UTC
    cfb = {"weeks": {"7": {"games": [{"game_id": 401, "gameday": "2026-10-24T16:00:00+00:00"}]}}}
    assert sources.game_starts(cfb, "cfb") == {"401": datetime(2026, 10, 24, 16, tzinfo=timezone.utc)}
    nba = {"schedule": [{"game_id": "401909921", "tip_off": "2026-11-03T03:00Z"}]}
    assert sources.game_starts(nba, "nba") == {"401909921": datetime(2026, 11, 3, 3, tzinfo=timezone.utc)}
    pl = {"fixture_detail_by_event_id": {"PK2W-AlCp-S0tf": {"commence_time": "2026-11-28T15:00:00Z"}}}
    assert sources.game_starts(pl, "pl") == {"PK2W-AlCp-S0tf": datetime(2026, 11, 28, 15, tzinfo=timezone.utc)}
    assert sources.game_starts(None, "nfl") == {}


# --- log: the cutoff and the stored fields -------------------------------------

def test_a_price_between_the_true_start_and_kalshis_end_is_not_logged():
    in_game = kalshi_row(occurrence=END, snapshot_ts="2026-10-18T18:30:00+00:00")
    made, skipped = build([in_game])
    assert made == [] and skipped.get("price_at_or_after_kickoff") == 1


def test_a_pre_game_price_stores_the_true_start_and_keeps_kalshis_value():
    made, _ = build([kalshi_row(occurrence=END, snapshot_ts="2026-10-18T16:00:00+00:00")])
    assert made[0]["kickoff"] == START.isoformat()
    assert made[0]["expected_end"] == END


def test_a_game_with_no_schedule_start_is_not_logged():
    from paper.log import candidate_rows
    games = sources.snapshot_games(nfl_snapshot(), "nfl")
    games["2026_06_BAL_CLE"]["start"] = None  # e.g. an NBA pick whose game is not in the schedule yet
    made, skipped = candidate_rows([kalshi_row(occurrence=END)], {"nfl": games}, config())
    assert made == [] and skipped.get("no_game_start") == 1


# --- report: old rows are corrected without being rewritten --------------------

def old_row(**over):
    """A pre-fix ledger row: kickoff holds Kalshi's end, no expected_end."""
    base = {"record": "candidate", "kind": "candidate", "id": "a", "sport": "nfl", "market": "moneyline",
            "game_id": "2026_06_BAL_CLE", "ticker": "KXNFLGAME-26OCT18BALCLE-BAL", "selection": "BAL",
            "side": "yes", "line": None, "model_p": 0.60, "market_price": 0.40, "fee": 0.02, "edge": 18.0,
            "segment": "nfl:moneyline:0.60-0.70", "stake": 5.0, "status": "settled", "outcome": "yes",
            "pnl_per_contract": 0.58, "price_observed_at": "2026-10-17T20:00:00+00:00",
            "logged_at": "2026-10-17T20:00:00+00:00", "kickoff": END}
    base.update(over)
    return base


STARTS = {("nfl", "2026_06_BAL_CLE"): START}


def build_report(rows, scores=(), starts=STARTS, **kw):
    return report.build(rows, closing={}, gate_cfg=gate_of(config()), score_rows=list(scores), starts=starts, **kw)


def test_old_rows_observed_after_the_true_start_are_excluded_and_counted():
    pre = old_row(id="pre")
    live = old_row(id="live", price_observed_at="2026-10-18T18:30:00+00:00")  # before Kalshi's end
    rep = build_report([pre, live])
    assert rep["totals"]["n_logged"] == 1
    assert rep["totals"]["excluded_in_play_rows"] == 1
    assert rep["per_sport"]["nfl"]["excluded_in_play_rows"] == 1


def test_a_post_fix_row_keeps_its_own_kickoff():
    row = old_row(kickoff=START.isoformat(), expected_end=END, price_observed_at="2026-10-18T16:00:00+00:00")
    assert build_report([row], starts={})["totals"]["n_logged"] == 1


def test_an_old_row_whose_game_is_in_no_schedule_is_excluded_and_counted():
    rep = build_report([old_row()], starts={})
    assert rep["totals"]["n_logged"] == 0
    assert rep["totals"]["excluded_unresolved_start_rows"] == 1


def test_score_rows_observed_in_play_are_excluded_too():
    score = {"record": "score", "id": "s", "sport": "nfl", "market": "moneyline", "game_id": "2026_06_BAL_CLE",
             "ticker": "T", "model_p": 0.6, "market_price": 0.4, "kickoff": END, "status": "settled",
             "outcome": "yes", "price_observed_at": "2026-10-18T19:00:00+00:00", "logged_at": "x"}
    rep = build_report([old_row()], scores=[score])
    assert rep["totals"]["excluded_in_play_rows"] == 1
    assert rep["scoring"]["total"]["n_rows"] == 1  # only the backfilled pre-game row remains


def test_nfl_total_rows_sharing_one_model_p_across_lines_are_excluded():
    def total(i, line, p):
        return old_row(id=i, market="total", line=line, ticker=f"KXNFLTOTAL-26OCT18BALCLE-{int(line)}",
                       model_p=p, segment="nfl:total:0.60-0.70", model_basis="normal")
    same = [total("a", 63.5, 0.653892), total("b", 60.5, 0.653892)]
    fine = [total("c", 55.5, 0.61), total("d", 52.5, 0.64)]
    rep = build_report(same + fine)
    assert rep["totals"]["excluded_same_p_rows"] == 2
    assert rep["totals"]["n_logged"] == 2


# --- closing -------------------------------------------------------------------

def test_the_closing_window_runs_off_the_true_start_and_skips_legacy_rows():
    now = START - timedelta(minutes=20)
    new = old_row(id="n", status="open", kickoff=START.isoformat(), expected_end=END)
    legacy = old_row(id="o", status="open", ticker="LEGACY")
    assert set(closing.due_tickers([new, legacy], now)) == {new["ticker"]}
    assert set(closing.due_tickers([legacy], now, STARTS)) == {"LEGACY"}  # re-derived from the schedule


def test_a_closing_observation_at_or_after_the_true_start_is_not_a_closing_line():
    obs = [{"ticker": old_row()["ticker"], "observed_at": "2026-10-18T20:42:49+00:00",
            "yes_price": 0.97, "no_price": 0.03}]
    rows = closing.observation_rows(obs, [old_row()], STARTS)
    assert stats.closing_prices(rows) == {}


def test_main_reads_the_site_line_files_and_closes_on_the_true_start(tmp_path):
    import gzip, json
    site = tmp_path / "sites" / "NFL_Predictor"
    (site / "data" / "lines").mkdir(parents=True)
    (site / "data").joinpath("public_snapshot.json").write_text(json.dumps(nfl_snapshot()))
    lines = [kalshi_row(snapshot_ts=ts, yes_ask=ask, occurrence=END)
             for ts, ask in (("2026-10-18T16:00:00+00:00", 0.43), ("2026-10-18T18:30:00+00:00", 0.97))]
    with gzip.open(site / "data" / "lines" / "k.jsonl.gz", "wt") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in lines))
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(old_row(status="open", outcome=None, pnl_per_contract=None)) + "\n")
    seg = tmp_path / "segments.json"
    seg.write_text(json.dumps(config()))
    out = tmp_path / "report.json"
    assert report.main(["--ledger", str(ledger), "--segments", str(seg), "--sites-root", str(tmp_path / "sites"),
                        "--closing", str(tmp_path / "none.jsonl"), "--out", str(out)]) == 0
    nfl = json.loads(out.read_text())["per_sport"]["nfl"]
    # 3c, inside the slippage cap (a 4c move would set the row aside as slippage_cap).
    assert nfl["n_clv"] == 1 and nfl["mean_clv_cents"] == 3.0  # the 16:00 line, not the in-game 0.97
