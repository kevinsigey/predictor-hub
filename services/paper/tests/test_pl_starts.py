"""Old PL rows resolve their true start from PL's tracking picks.

A played PL fixture drops out of the public snapshot (replaced by a
`backfill-*` id), so an old ledger row's game_id is in no schedule and
the report excluded the row as `unresolved_start` -- PL scoring was
empty. The row's Kalshi ticker still carries the fixture (teams and
date), and PL_Predictor's tracking_picks.jsonl keeps every fixture's
commence_time, so the start is recovered from the fixture, never
guessed from Kalshi's occurrence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from paper import report, sources

COMMENCE = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)

OLD_PL_ROW = {
    "sport": "pl", "record": "candidate", "game_id": "2jSm-Akzh-CVGi",
    "event_ticker": "KXEPLGAME-26OCT25MUNBOU",
    "ticker": "KXEPLGAME-26OCT25MUNBOU-MUN",
    # Kalshi's expected end, ~3h after the true start: the old rows'
    # kickoff, which must NOT be trusted as the start.
    "kickoff": "2026-10-25T17:00:00+00:00",
    "price_observed_at": "2026-10-18T03:41:33+00:00",
}


def _picks_file(tmp_path, rows):
    path = tmp_path / "tracking_picks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def _pick(**over):
    pick = {"commence_time": "2026-10-25T14:00:00",
            "event_id": "backfill-Man United-Bournemouth-2026-10-25",
            "team_home": "Man United", "team_away": "Bournemouth",
            "market": "1x2", "outcome_name": "home_win"}
    pick.update(over)
    return pick


def test_tracking_starts_index_by_fixture(tmp_path):
    path = _picks_file(tmp_path, [_pick(), _pick(outcome_name="draw"),
                                  _pick(commence_time="2026-10-25T14:00:00",
                                        team_home="Arsenal", team_away="Chelsea")])
    starts = sources.pl_tracking_starts(path)
    # Several picks per fixture collapse to one entry; a second fixture
    # on the same day is its own entry.
    assert starts[("Man United", "Bournemouth", "2026-10-25")] == COMMENCE
    assert starts[("Arsenal", "Chelsea", "2026-10-25")] == COMMENCE
    assert len(starts) == 2


def test_tracking_starts_missing_file_is_empty(tmp_path):
    assert sources.pl_tracking_starts(tmp_path / "nope.jsonl") == {}


def test_an_old_pl_row_resolves_through_its_fixture(tmp_path):
    starts = {("pl-teams",) + k: v for k, v in
              sources.pl_tracking_starts(_picks_file(tmp_path, [_pick()])).items()}
    assert sources.row_start(OLD_PL_ROW, starts) == COMMENCE


def test_an_old_pl_row_with_no_tracking_fixture_stays_unresolved():
    assert sources.row_start(OLD_PL_ROW, {}) is None
    other = {("pl-teams", "Arsenal", "Chelsea", "2026-10-25"): COMMENCE}
    assert sources.row_start(OLD_PL_ROW, other) is None


def test_the_fixture_key_comes_from_the_ticker_not_the_kalshi_time():
    key = sources._pl_fixture_key(OLD_PL_ROW)
    assert key == ("Man United", "Bournemouth", "2026-10-25")
    # A club outside the map, or a malformed ticker, resolves nothing.
    assert sources._pl_fixture_key({"ticker": "KXEPLGAME-26OCT25XXXBOU-XXX"}) is None
    assert sources._pl_fixture_key({"ticker": "not-a-ticker"}) is None
    assert sources._pl_fixture_key({}) is None


def test_exclusions_keep_an_old_pl_row_priced_before_its_true_start(tmp_path):
    starts = {("pl-teams",) + k: v for k, v in
              sources.pl_tracking_starts(_picks_file(tmp_path, [_pick()])).items()}
    kept, _, counts = report.apply_exclusions([dict(OLD_PL_ROW)], [], starts)
    assert kept == [OLD_PL_ROW]
    assert counts["unresolved_start"] == {}

    in_play = dict(OLD_PL_ROW, price_observed_at="2026-10-25T15:30:00+00:00")
    kept, _, counts = report.apply_exclusions([in_play], [], starts)
    assert kept == []
    assert counts["in_play"] == {"pl": 1}
