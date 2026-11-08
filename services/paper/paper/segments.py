"""The segment allow-list, confidence buckets, and the edge-claim gate.

A "segment" is the unit an edge claim is made about: sport x market x confidence
bucket. Confidence is bucketed rather than used raw because a claim about
"p = 0.63" is a claim about one number, while a claim about the 0.60-0.70 band
is about a population you can accumulate.

The allow-list is the other half. A market that is not on it is never logged as
a candidate — it can still be logged as a shadow, with no stake, so that a
segment nobody expected to work can be evaluated before anyone is allowed to bet
on it. Shadow rows exist to be counted later, not to be sized now.

The gate numbers live in `data/paper/segments.json` rather than in code so they
can be raised without a code change, and `tests/test_gate.py` fails if the
committed minimum is ever LOWERED. A gate that can be lowered by an edit is not
a gate.
"""
from __future__ import annotations

import json
from pathlib import Path

SEGMENTS_PATH = Path("data/paper/segments.json")

# Net edge, in percentage points, that a market must clear after fees before it
# is logged as a candidate at all. Pre-stated, not tuned: see the PR description.
DEFAULT_EDGE_POINTS = 4.0

# Half-open [lo, hi) bands over model_p. The top band's upper bound is above 1.0
# so a model_p of exactly 1.0 still lands in a bucket.
DEFAULT_BUCKETS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01)]

# The floor the brief sets for an "has an edge" claim. Not configurable downward.
MIN_SETTLED_FLOOR = 300

# A second floor, on GAMES rather than bets, met ALONGSIDE the one above.
#
# A Kalshi ladder is many contracts on one game: a CFB game has a moneyline, a
# spread ladder and a total ladder, and the live ledger holds 239 CFB candidates
# from 44 games. So 300 settled BETS can be about twenty-five games of
# evidence, and a segment could be certified on noise. This floor counts the
# games, so the evidence has to be spread across real games to count.
#
# It is ADDED to the bet floor, not a replacement for it: both have to be met.
MIN_SETTLED_GAMES_FLOOR = 100


class SegmentError(RuntimeError):
    """segments.json exists but cannot be used as an allow-list."""


def bucket_label(model_p: float, buckets=None) -> str | None:
    """The confidence band a probability falls in, as "0.60-0.70"."""
    for lo, hi in (buckets or DEFAULT_BUCKETS):
        if lo <= model_p < hi:
            return f"{lo:.2f}-{hi:.2f}" if hi <= 1.0 else f"{lo:.2f}+"
    return None


def segment_key(sport: str, market: str, bucket: str) -> str:
    return f"{sport}:{market}:{bucket}"


def load(path: Path | str = SEGMENTS_PATH) -> dict:
    """Read the allow-list. A missing file is an error, never an empty allow-list.

    An empty or defaulted allow-list would silently log nothing and read as "no
    segment qualified", which is a claim about the world. Failing loudly is the
    only honest behaviour here.
    """
    path = Path(path)
    if not path.is_file():
        raise SegmentError(f"no segment allow-list at {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SegmentError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("segments"), list):
        raise SegmentError(f"{path} has no 'segments' list")
    return config


def edge_threshold(config: dict) -> float:
    return float(config.get("edge_threshold_points", DEFAULT_EDGE_POINTS))


def max_spread_points(config: dict) -> float:
    return float(config.get("max_spread_points", 8.0))


def buckets(config: dict, sport: str | None = None) -> list[tuple[float, float]]:
    """The confidence bands a row's probability is measured against.

    A sport may have bands of its own, because a sport's probabilities have
    their own shape: F1 spreads one race over two dozen drivers, so its best
    race-winner probability is around 14%, and the 0.50+ bands that suit a
    two-sided game would exclude every F1 contract there is. A sport with no
    bands of its own uses the config's, and the config falls back to the
    built-in ones, so nothing changes for a sport that has not asked for
    anything different.
    """
    per_sport = config.get("confidence_buckets_by_sport") or {}
    raw = (per_sport.get(sport) if sport else None) or config.get("confidence_buckets")
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_BUCKETS)
    return [(float(lo), float(hi)) for lo, hi in raw]


def match(config: dict, *, sport: str, market: str, position: str | None = None) -> dict | None:
    """The allow-list entry covering this market, or None.

    An entry may narrow itself by position, which is how "NFL WR/TE receptions"
    is expressed as one entry that only covers receivers and tight ends. An entry
    with no `positions` covers the market for everyone.
    """
    for entry in config.get("segments", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("sport") != sport or entry.get("market") != market:
            continue
        positions = entry.get("positions")
        if positions and position not in positions:
            continue
        return entry
    return None


def gate(config: dict) -> dict:
    """The edge-claim gate: minimum settled bets AND games, and the other two conditions."""
    raw = config.get("gate") or {}
    return {
        "min_settled": int(raw.get("min_settled", MIN_SETTLED_FLOOR)),
        # The second floor, on games. Met alongside min_settled, never instead
        # of it: 300 bets from 25 games is still only 25 games of evidence.
        "min_settled_games": int(raw.get("min_settled_games", MIN_SETTLED_GAMES_FLOOR)),
        "require_ci_excludes_zero": bool(raw.get("require_ci_excludes_zero", True)),
        "require_calibration_not_widening": bool(raw.get("require_calibration_not_widening", True)),
        # Below this the "is the gap widening" comparison is two numbers from a
        # handful of bets, so the report says "not yet measurable" instead.
        "min_for_calibration_trend": int(raw.get("min_for_calibration_trend", 100)),
    }


#: Pre-stated, NOT tuned on the ledger. Used when segments.json has no `sport_gate`.
SPORT_GATE_DEFAULTS = {"min_settled_bets": 100,
                       "min_events": {"default": 50, "f1": 20, "pl": 60}}


def sport_gate(config: dict) -> dict:
    """The sport-level verdict config (segments.json `sport_gate`); the segment gate is separate."""
    raw = config.get("sport_gate") or {}
    events = dict(SPORT_GATE_DEFAULTS["min_events"])
    events.update(raw.get("min_events") or {})
    return {"min_settled_bets": int(raw.get("min_settled_bets", SPORT_GATE_DEFAULTS["min_settled_bets"])),
            "min_events": {k: int(v) for k, v in events.items()}}
