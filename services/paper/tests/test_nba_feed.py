"""The NBA picks feed the game detail reads for its matchup probabilities.

Unlike NFL and PL, NBA publishes no per-game probability in its public
snapshot -- only aggregate model metrics. The daily refresh therefore appends
every pick it scores to `NBA_Predictor/data/picks_history.jsonl.gz`, and this
module turns those rows into one view-model per game: the latest pick the
model has published, stamped with its own `created_at` so the ledger can tell
whether the price was logged before or after the pick.

A pick made after the price is a shadow: logged, countable, never staked.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from conftest import config

from paper import sources as facts_mod
from paper.log import candidate_rows

KICKOFF = "2026-11-01T19:00:00+00:00"
PRICE_TS = "2026-11-01T12:30:00+00:00"
SCHEDULE = {"schedule": [{"game_id": "g1", "tip_off": "2026-11-01T19:00Z"}]}  # the site's tip_off


def _pick(
    game_id: str,
    created_at: str,
    home_win_prob: float = 0.60,
    predicted_margin: float = 3.0,
    predicted_total: float = 220.0,
    total_sigma: float = 15.0,
    model_version: str = "v1",
) -> dict:
    return {
        "game_id": game_id,
        "game_date": "2026-11-01",
        "home_team": "BOS",
        "away_team": "MIA",
        "home_win_prob": home_win_prob,
        "predicted_margin": predicted_margin,
        "predicted_total": predicted_total,
        "total_sigma": total_sigma,
        "model_version": model_version,
        "created_at": created_at,
    }


def _write_picks(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _kalshi_row(**over: object) -> dict:
    """One Out player row: BOS at MIA, priced before kickoff."""
    row = {
        "snapshot_ts": PRICE_TS,
        "series": "KXNBAGAME",
        "stat": "moneyline",
        "event_ticker": "KXNBAGAME-26NOV01BOSMIA",
        "ticker": "KXNBAGAME-26NOV01BOSMIA-BOS",
        "player": None,
        "line": None,
        "yes_bid": 0.38,
        "yes_ask": 0.40,
        "no_bid": 0.58,
        "no_ask": 0.62,
        "last": 0.40,
        "volume": 100.0,
        "open_interest": 100.0,
        "status": "active",
        "result": None,
        "settlement_value": None,
        "occurrence": KICKOFF,
        "close_time": "2026-11-03T19:00:00+00:00",
    }
    row.update(over)
    return row


def _root_with_picks(tmp_path: Path, rows: list[dict]) -> Path:
    root = tmp_path / "sites"
    (root / "NBA_Predictor" / "data").mkdir(parents=True)
    _write_picks(root / "NBA_Predictor" / "data" / "picks_history.jsonl.gz", rows)
    return root


def test_snapshot_games_reads_the_picks_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _root_with_picks(
        tmp_path,
        [
            _pick("g1", "2026-10-18T12:00:00+00:00", 0.60),
            _pick("g1", "2026-10-19T13:00:00+00:00", 0.55),
            _pick("g2", "2026-10-18T12:00:00+00:00", 0.70),
        ],
    )
    monkeypatch.setenv("SITES_ROOT", str(root))

    games = facts_mod.snapshot_games(SCHEDULE, "nba")

    assert set(games) == {"g1", "g2"}
    g1 = games["g1"]
    assert g1["sport"] == "nba"
    assert g1["home"] == "BOS" and g1["away"] == "MIA"
    assert g1["date"] == "2026-11-01"
    # The latest pick wins; its own timestamp is what the price must predate.
    assert g1["generated_at"] == "2026-10-19T13:00:00+00:00"
    assert g1["model_fingerprint"] == "v1"
    assert g1["probs"]["home_win"] == 0.55
    assert g1["probs"]["away_win"] == pytest.approx(0.45)
    assert g1["total_mu"] == 220.0
    assert g1["total_sigma"] == 15.0
    g2 = games["g2"]
    assert g2["probs"]["home_win"] == 0.70


def test_snapshot_games_is_empty_without_a_picks_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SITES_ROOT", str(tmp_path / "sites"))
    assert facts_mod.snapshot_games(SCHEDULE, "nba") == {}


def test_a_pick_made_after_the_price_is_a_shadow_never_staked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _root_with_picks(tmp_path, [_pick("g1", "2026-11-01T14:00:00+00:00", 0.60)])
    monkeypatch.setenv("SITES_ROOT", str(root))

    games = facts_mod.snapshot_games(SCHEDULE, "nba")
    made, skipped = candidate_rows([_kalshi_row()], {"nba": games}, config())

    assert len(made) == 1
    assert made[0]["kind"] == "shadow"
    assert made[0]["stake"] == 0.0
    assert made[0]["model_ordering_proven"] is False
    assert made[0]["model_p"] == 0.60
    assert skipped == {}


def test_a_pick_made_before_the_price_is_a_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _root_with_picks(tmp_path, [_pick("g1", "2026-10-18T12:00:00+00:00", 0.60)])
    monkeypatch.setenv("SITES_ROOT", str(root))

    games = facts_mod.snapshot_games(SCHEDULE, "nba")
    made, skipped = candidate_rows([_kalshi_row()], {"nba": games}, config())

    assert len(made) == 1
    assert made[0]["kind"] == "candidate"
    assert made[0]["stake"] > 0.0
    assert made[0]["model_ordering_proven"] is True
    assert made[0]["model_p"] == 0.60
    assert made[0]["market_price"] == 0.40
    assert made[0]["segment"] == "nba:moneyline:0.60-0.70"
    assert skipped == {}
