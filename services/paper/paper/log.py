"""Turn Kalshi line snapshots into candidate rows, or refuse to.

This is where the no-hindsight rules live, so it is worth stating all four in
one place before the code:

  1. `logged_at` is the Kalshi row's OWN `snapshot_ts`, never the wall clock at
     the moment the logger happens to run. A row observed on Tuesday and written
     on Saturday is a Tuesday row.
  2. A row is only eligible if `snapshot_ts` is strictly before kickoff, read
     from the row's `occurrence` field. Unreadable means ineligible.
  3. The price logged is the ask from THAT SAME row. No other snapshot's price is
     ever consulted for a candidate.
  4. The model probability must come from a snapshot whose `generated_at` is at
     or before the price's `snapshot_ts`, using the latest such snapshot. If that
     ordering cannot be established the row is logged as a shadow, never as a
     candidate — a probability computed after we saw the price is hindsight with
     extra steps.

Rules 1-3 stop a post-kickoff price entering. Rule 4 stops a post-price
probability entering. They are different leaks and need different guards.

F1 adds a fifth guard of its own, because its contracts are not games. A
Kalshi F1 contract is one DRIVER's contract, so the probability is that
driver's own probability for that race; and Kalshi's F1 occurrence runs
about six hours later than the session it names, so the cutoff is the
earlier of Kalshi's occurrence and the session time the snapshot knows.

PAPER ONLY. Nothing here places an order; the only price written is one that was
publicly observable at the time it is stamped with.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone

from . import sources
from .fees import kalshi_fee_cents
from .ledger import (KIND_CANDIDATE, KIND_SHADOW, RECORD_SCORE, STATUS_OPEN, candidate_id,
                     score_id, stake_for)
from . import segments as segments_mod
from .segments import bucket_label, edge_threshold, match, segment_key

# Why a row was not logged. Counted, never printed per row: a report that lists
# 6,000 rejections is unreadable, and one that lists none hides a misconfigured
# join that is silently logging nothing at all.
SKIP_REASONS = (
    "unreadable_snapshot_ts",
    "no_kickoff",
    "price_at_or_after_kickoff",
    "not_a_game_market",
    "no_matching_game",
    "no_model_probability",
    "no_bid",
    "spread_too_wide",
    "zero_volume",
    "dead_quote",
    "below_edge_threshold",
    # F1: a Kalshi F1 contract is one driver's contract, so it has its own
    # reasons for not being logged.
    "f1_unpriceable_series",
    "f1_no_matching_race",
    "f1_no_driver",
    "f1_hindsight_prediction",
)


def _f1_candidate(row: dict, parts: dict, games: dict):
    """A Kalshi F1 row resolved to a race, a driver, and a probability.

    Returns `(resolved, reason)`. `resolved` is None when the row cannot be
    priced at all, and `reason` says which of the four F1 reasons it was:
    the series is one the model publishes no probability for, the event
    ticker names no race in the snapshot, the title names no single driver,
    or the prediction is one the model published after the session it is
    about.

    F1 is not a game market: every Kalshi F1 contract is ONE DRIVER's
    contract, so the probability is that driver's own probability for that
    race, looked up by driver and race rather than by home and away.
    """
    market = sources.F1_SERIES_MARKETS.get(parts.get("series"))
    if market is None:
        return None, "f1_unpriceable_series"
    spec = sources.F1_MARKETS[market]
    scope = spec["scope"]
    if scope == "season":
        game = (games or {}).get("championship")
        # The contract has to be for the season the snapshot is about: a 2025
        # championship market priced with the 2026 championship probability
        # would be a different contract entirely.
        season = sources.f1_season(parts)
        if game is not None and season is not None and season != game.get("season"):
            return None, "f1_no_matching_race"
    else:
        game = sources.join_f1_race(row, games, scope)
    if game is None:
        return None, "f1_no_matching_race"
    session = (game.get("sessions") or {}).get(scope) or {}
    # A prediction the model published after the session it is about is not a
    # forward-looking prediction: a backtest is a replay computed once the
    # race was over, and a rebuild is a stored row the tracking store cannot
    # vouch for. Neither may be paired with a live price.
    if session.get("source") in sources.F1_HINDSIGHT_SOURCES:
        return None, "f1_hindsight_prediction"
    tiers = spec["tiers"]
    if tiers is not None and session.get("tier") not in tiers:
        return None, "f1_hindsight_prediction"
    driver = sources.f1_driver(row.get("title"), game.get("drivers"))
    if driver is None:
        return None, "f1_no_driver"
    model_p = (game.get("probs") or {}).get(driver, {}).get(market)
    if not isinstance(model_p, (int, float)):
        return None, "no_model_probability"
    start = sources.parse_ts(session.get("start"))
    if start is None:
        return None, "f1_no_matching_race"
    basis = (f"F1 snapshot {spec['field']} for {driver} "
             f"({scope} session, tier {session.get('tier')})")
    return (game, market, driver, float(model_p), basis, start), None


def p_over_line(mu, sigma, line) -> float | None:
    """P(total > line) for a Normal(mu, sigma) game total; None if any input is unusable.

    A Kalshi total ladder has one rung per threshold, so the probability must be
    evaluated at the rung's own line. The snapshot's single `over_prob` is for the
    book's line and is wrong for every other rung.
    """
    try:
        mu, sigma, line = float(mu), float(sigma), float(line)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(mu) and math.isfinite(sigma) and math.isfinite(line)) or sigma <= 0:
        return None
    return 0.5 * (1.0 - math.erf((line - mu) / sigma / math.sqrt(2.0)))


TOTAL_MODEL_BASIS = "normal(predicted_total, total_sigma) at the rung line"

#: The basis recorded on a row priced from PL's joint score grid.
SCORE_GRID_MODEL_BASIS = "score_grid at the rung line"

#: The basis recorded on a row priced from PL's published BTTS probability.
BTTS_MODEL_BASIS = "published btts_yes_prob"


#: The basis recorded on a row priced from the model's margin.
SPREAD_MODEL_BASIS = "normal(predicted_margin, sigma) at the rung line"


def p_covers_line(mu, sigma, line) -> float | None:
    """P(margin > line) for a Normal(mu, sigma) margin; None if unusable.

    Kalshi's spread ladder is a MARGIN market -- "Dartmouth wins by over 6.5
    points" -- so the probability is the chance the team's margin exceeds the
    rung's own line. Every rung has its own line, so one figure cannot price
    the ladder.
    """
    return p_over_line(mu, sigma, line)


def _negate(value):
    """The negative of a number, or None if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return -float(value)


def spread_side(row: dict, game: dict) -> str | None:
    """Which side of the game a spread rung is about, or None.

    A spread rung is named after a TEAM ("Dartmouth wins by over 6.5 points",
    "San Antonio wins by over 8.5 points"), not after a side, so the side has
    to be worked out from the rung.

    Two ways to work it out, and the ticker is tried first because it is exact.

    **The ticker suffix.** Kalshi writes the team's code with the line's own
    integer appended: `SAS9` for "over 8.5 points", `DART7` for "over 6.5
    points". Every spread rung in both leagues' committed line files follows
    that shape -- 1,896 NBA rows and 2,185 CFB rows, checked -- so the code is
    the suffix minus that integer, and it is compared with the game's two team
    codes. The NBA's schedule uses those same codes, so it matches exactly.

    **The title.** The fallback, for a game whose codes the ledger does not
    have. The title is compared against the team's name AS KALSHI SPELLS IT,
    not as the model's snapshot spells it: Kalshi writes "Northern Colorado"
    where CFBD writes "Northern Colo.", and "Ohio St." where CFBD writes "Ohio
    State". Matching the title against the model's spelling would refuse most
    of the spread ladder over a spelling difference.

    A rung about a team that is in neither the game's codes nor its names is
    refused: a spread priced with the other team's probability is worse than no
    row at all.
    """
    by_code = _spread_side_by_code(row, game)
    if by_code is not None:
        return by_code

    title = row.get("title")
    if not isinstance(title, str):
        return None
    subject = title.split(" wins by over", 1)[0].strip()
    if not subject:
        return None
    for side in ("home", "away"):
        for name in (game.get(f"{side}_kalshi_name"), game.get(f"{side}_name")):
            if name and subject == name:
                return side
    return None


def _spread_side_by_code(row: dict, game: dict) -> str | None:
    """The side, from the rung's ticker suffix. None when it cannot be read.

    The suffix is `<team code><int(line) + 1>`, so the code is the suffix with
    that integer removed. The integer is not guessed at: the rung's own `line`
    says what it is, and if the suffix does not end with it, the suffix is
    something this does not understand and the caller falls back to the title.
    """
    ticker = row.get("ticker")
    event = row.get("event_ticker")
    line = row.get("line")
    if not isinstance(ticker, str) or not isinstance(event, str):
        return None
    if not ticker.startswith(event + "-"):
        return None
    suffix = ticker[len(event) + 1:]
    try:
        tail = str(int(float(line)) + 1)
    except (TypeError, ValueError):
        return None
    if not suffix.endswith(tail):
        return None
    code = suffix[:-len(tail)]
    if not code:
        return None
    matches = [side for side in ("home", "away") if game.get(side) == code]
    # Two matches would mean the game's two teams have the same code, which
    # cannot be resolved; one match is the answer; none means the rung is
    # about a team that is not in this game.
    return matches[0] if len(matches) == 1 else None


def _iso(stamp: datetime | None) -> str | None:
    return stamp.isoformat() if stamp else None


def candidate_rows(rows: list[dict], games_by_sport: dict[str, dict], config: dict, *,
                   committed_by_game: dict[str, float] | None = None,
                   positions: dict[str, str] | None = None,
                   scores: list[dict] | None = None) -> tuple[list[dict], dict[str, int]]:
    """Build the candidate and shadow rows for one batch of Kalshi snapshots.

    Returns the rows to append and a tally of why each rejected row was rejected.
    If `scores` is a list, every pre-kickoff row that has a model probability
    (ordered before the price) and a buyable price is appended to it as a raw
    observation, BEFORE any liquidity or edge filter; `score_records` turns
    those into the no-stake score rows.
    """
    best: dict[str, dict] = {}
    skipped: dict[str, int] = {}
    threshold = edge_threshold(config)

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for row in rows:
        parts = sources.event_parts(row.get("event_ticker"))
        if parts is None or parts["sport"] is None:
            skip("no_matching_game")
            continue
        sport, market = parts["sport"], row.get("stat")

        # -- no-hindsight 1 and 2: the price must be readable and pre-kickoff ---
        seen_at = sources.parse_ts(row.get("snapshot_ts"))
        if seen_at is None:
            skip("unreadable_snapshot_ts")
            continue
        start = sources.kickoff(row)
        if start is None:
            skip("no_kickoff")
            continue
        if seen_at >= start:
            skip("price_at_or_after_kickoff")
            continue

        if sport == "f1":
            # A Kalshi F1 contract is one driver's contract, so it is priced
            # from that driver's own probability, looked up by race and
            # driver. The market is always the YES side: "Main Race: Max
            # Verstappen wins" is the YES contract on Verstappen winning.
            side = "yes"
            resolved, reason = _f1_candidate(row, parts, games_by_sport.get(sport) or {})
            if resolved is None:
                skip(reason)
                continue
            game, market, pick, model_p, model_basis, session_start = resolved
            # Kalshi's F1 occurrence runs about six hours later than the
            # session it names, so the EARLIER of the two is the cutoff: a
            # price seen after the real start is not a pre-session price,
            # however Kalshi's own field reads.
            start = min(start, session_start)
            if seen_at >= start:
                skip("price_at_or_after_kickoff")
                continue
        else:
            if market not in sources.GAME_LEVEL_STATS:
                # Player props need a calibrated per-prop probability, which no site
                # publishes yet. Deliberately not guessed at.
                skip("not_a_game_market")
                continue

            game = sources.join_games(parts, games_by_sport.get(sport) or {})
            if game is None:
                skip("no_matching_game")
                continue

            # A Kalshi "<TEAM> wins" rung is the YES contract on that team.
            side = "yes"
            pick = sources.selection(row)
            probs = game.get("probs") or {}
            model_p = None
            model_basis = None
            if market == "total":
                # "over X points" YES contract: P(total > X) from the model's
                # predicted total and sigma, at THIS rung's line.
                #
                # PL publishes a joint score grid instead, and the grid is the
                # better distribution to price a goal total from: a goal count is
                # skewed and discrete, and a Normal fit of it was wrong by up to
                # four points at the middle rungs, which is the size of the edge
                # threshold itself. So a grid, when there is one, is used.
                grid = game.get("score_grid")
                if grid:
                    model_p = sources.grid_p_over(grid, row.get("line"))
                    model_basis = SCORE_GRID_MODEL_BASIS
                else:
                    model_p = p_over_line(game.get("total_mu"), game.get("total_sigma"), row.get("line"))
                    model_basis = TOTAL_MODEL_BASIS
            elif market == "btts":
                # "Both Teams To Score" YES contract: P(both score), published
                # outright by PL. No side is inferred from the grid.
                model_p = probs.get("btts_yes")
                model_basis = BTTS_MODEL_BASIS
            elif market == "spread":
                # "<Team> wins by over X points" YES contract: P(that team's
                # margin > X), from the model's predicted margin and sigma, at
                # THIS rung's line. The away team's margin is the negative of
                # the model's home margin, so the sign follows the side the
                # rung is about.
                side_of_rung = spread_side(row, game)
                if side_of_rung == "home":
                    model_p = p_covers_line(game.get("margin_mu"), game.get("margin_sigma"), row.get("line"))
                elif side_of_rung == "away":
                    model_p = p_covers_line(
                        _negate(game.get("margin_mu")), game.get("margin_sigma"), row.get("line"))
                else:
                    model_p = None
                model_basis = SPREAD_MODEL_BASIS
            elif sport == "pl":
                # PL moneylines have three outcomes: home win, tie, away win. Each is
                # its own market (YES = that outcome), so the model probability is
                # looked up by the selection code, not by home/away.
                model_p = probs.get("draw") if pick == "TIE" else (
                    probs.get("home_win") if pick == game.get("home") else
                    probs.get("away_win") if pick == game.get("away") else None)
            else:
                if pick and pick == game.get("home"):
                    model_p = probs.get("home_win")
                elif pick and pick == game.get("away"):
                    model_p = probs.get("away_win")
            if not isinstance(model_p, (int, float)):
                skip("no_model_probability")
                continue
            model_p = float(model_p)

        # -- no-hindsight 4: the probability must predate the price -------------
        proven = _model_precedes_price(game.get("generated_at"), seen_at)

        price = sources.buyable_price(row, side)
        if price is None:
            skip("no_matching_game")
            continue

        if scores is not None and proven:
            allowed = match(config, sport=sport, market=market, position=(positions or {}).get(pick))
            scores.append({"sport": sport, "market": market, "game_id": game["game_id"],
                           "ticker": row.get("ticker"), "selection": pick, "line": row.get("line"),
                           "model_p": model_p, "price": price, "seen_at": seen_at,
                           "yes_bid": row.get("yes_bid"), "kickoff": start, "home": game.get("home"),
                           "fingerprint": game.get("model_fingerprint"),
                           "allow_listed": allowed is not None})

        # -- liquidity: a quote you cannot actually trade is not a price ------
        # The ask alone is not enough. A market with no bid, a wide spread, no
        # volume, or a 1-cent ask is not a market anybody can enter, and logging
        # it would report an edge that was never available to anyone.
        yes_bid = row.get("yes_bid")
        no_bid = row.get("no_bid")
        volume = row.get("volume")
        open_interest = row.get("open_interest")
        if not isinstance(yes_bid, (int, float)) or float(yes_bid) <= 0:
            skip("no_bid")
            continue
        spread = price - float(yes_bid)
        max_spread = segments_mod.max_spread_points(config) / 100.0
        if spread > max_spread:
            skip("spread_too_wide")
            continue
        if not isinstance(volume, (int, float)) or float(volume) <= 0:
            skip("zero_volume")
            continue
        if price <= 0.01 or price >= 0.99:
            skip("dead_quote")
            continue

        fee_cents = kalshi_fee_cents(price * 100.0, contracts=1)
        fee = fee_cents / 100.0
        edge = round((model_p - price - fee) * 100.0, 4)
        if edge < threshold:
            skip("below_edge_threshold")
            continue

        bucket = bucket_label(model_p, segments_mod.buckets(config, sport))
        if bucket is None:
            skip("below_edge_threshold")
            continue
        segment = segment_key(sport, market, bucket)
        position = (positions or {}).get(pick)
        allow = match(config, sport=sport, market=market, position=position)

        # Allow-listed AND the model/price ordering is provable: a real candidate.
        # Anything else clears the same edge bar but earns no stake.
        kind = KIND_CANDIDATE if (allow is not None and proven) else KIND_SHADOW
        game_id = game["game_id"]
        row_id = candidate_id(sport=sport, game_id=game_id, market=market,
                              selection=pick, side=side, line=row.get("line"))

        made = {
            "record": KIND_CANDIDATE,  # shadows are candidate rows flagged shadow
            "id": row_id,
            "kind": kind,
            "sport": sport,
            "market": market,
            "game_id": game_id,
            "event_ticker": row.get("event_ticker"),
            "ticker": row.get("ticker"),
            # For F1 the contract IS about a driver, so the driver is the
            # player; for every other sport the model prices the game, not
            # the player, and the field stays empty.
            "player": pick if sport == "f1" else None,
            "selection": pick,
            "side": side,
            "line": row.get("line"),
            "model_p": round(model_p, 6),
            "model_basis": model_basis,
            "market_price": price,
            "price_observed_at": _iso(seen_at),
            "kickoff": _iso(start),
            "fee_cents": fee_cents,
            "fee": round(fee, 6),
            "edge": edge,
            "segment": segment,
            "script_bucket": None,
            "yes_bid": _num(row.get("yes_bid")),
            "no_bid": _num(row.get("no_bid")),
            "spread_points": round(spread * 100.0, 4),
            "volume": _num(volume),
            "open_interest": _num(open_interest),
            "model_fingerprint": game.get("model_fingerprint"),
            "stake": 0.0,
            "logged_at": _iso(seen_at),
            "model_generated_at": game.get("generated_at"),
            "model_ordering_proven": proven,
            "status": STATUS_OPEN,
            "outcome": None,
            "pnl_per_contract": None,
        }
        _keep_best(best, made)

    # Stakes are assigned AFTER the batch is deduplicated, in observation order,
    # so the correlated-exposure cap is spent earliest-bet-first and a bet that
    # loses its place to a better-evidenced observation never consumed budget.
    committed = dict(committed_by_game or {})
    for made in sorted(best.values(), key=lambda r: r["logged_at"]):
        if made["kind"] != KIND_CANDIDATE:
            continue
        made["stake"] = stake_for(made["model_p"], made["market_price"],
                                  game_id=made["game_id"], committed_by_game=committed)
        committed[made["game_id"]] = committed.get(made["game_id"], 0.0) + made["stake"]
    return list(best.values()), skipped


def score_records(observations: list[dict]) -> list[dict]:
    """One no-stake "score" row per (game, market): the earliest observation, one main rung.

    Main rung: moneyline = the home side; every other game market (spread, total,
    btts) = the rung whose buyable price is closest to 0.50. Among the rungs seen
    at the earliest qualifying snapshot. F1 driver markets: one row per
    (race, ticker), earliest observation each, only for allow-listed markets.
    Stake is 0 and the row is its own record type, so it is never a bet.
    """
    groups: dict[tuple, list[dict]] = {}
    for o in observations:
        if o["sport"] == "f1":
            if not o["allow_listed"]:
                continue
            key = (o["sport"], o["game_id"], o["market"], o["ticker"])
        else:
            if o["market"] == "moneyline" and o["selection"] != o["home"]:
                continue
            key = (o["sport"], o["game_id"], o["market"], None)
        groups.setdefault(key, []).append(o)

    out = []
    for (sport, game_id, market, ticker), obs in groups.items():
        first = min(o["seen_at"] for o in obs)
        pick = min((o for o in obs if o["seen_at"] == first), key=lambda o: abs(o["price"] - 0.5))
        bid, ask = pick["yes_bid"], pick["price"]
        mid = (float(bid) + ask) / 2.0 if isinstance(bid, (int, float)) and 0 < float(bid) <= ask else None
        out.append({
            "record": RECORD_SCORE,
            "id": score_id(sport=sport, game_id=game_id, market=market, ticker=ticker),
            "sport": sport, "market": market, "game_id": game_id,
            "ticker": pick["ticker"], "side": "yes", "line": pick["line"],
            "model_p": round(pick["model_p"], 6),
            "market_price": ask,
            "market_mid": None if mid is None else round(mid, 6),
            "kickoff": _iso(pick["kickoff"]),
            "logged_at": _iso(pick["seen_at"]),
            "price_observed_at": _iso(pick["seen_at"]),
            "model_fingerprint": pick["fingerprint"],
            "stake": 0.0,
        })
    return out


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _keep_best(best: dict[str, dict], made: dict) -> None:
    """One row per bet within a batch. Which observation of a bet wins:

    1. a stakeable candidate beats a shadow, even if it was seen later;
    2. otherwise the earliest observation wins.

    Rule 1 is the one that is not obvious. A bet can be seen first at a price
    that predates the model snapshot, which makes it a shadow, and again later
    with the ordering provable. Logging the shadow first would permanently fix
    the id to a row with no stake and no proven ordering, and the later,
    perfectly valid candidate would be swallowed as a duplicate. That is a data
    QUALITY rule, not a hindsight rule: both observations happened before
    kickoff, and neither knows the result.
    """
    held = best.get(made["id"])
    if held is None:
        best[made["id"]] = made
        return
    held_is_candidate = held["kind"] == KIND_CANDIDATE
    if (made["kind"] == KIND_CANDIDATE) and not held_is_candidate:
        best[made["id"]] = made
    elif (made["kind"] == KIND_CANDIDATE) == held_is_candidate and \
            made["logged_at"] < held["logged_at"]:
        best[made["id"]] = made


def _model_precedes_price(generated_at, seen_at: datetime) -> bool:
    """True when the model's snapshot is no newer than the price we logged.

    Equality is allowed: a snapshot and a price stamped in the same second is
    not an ordering violation, and requiring strict inequality would throw away
    every candidate collected in the same instant as the snapshot refresh.
    """
    stamp = sources.parse_ts(generated_at)
    if stamp is None:
        return False
    return stamp <= seen_at


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from . import segments as segments_mod
    from .ledger import Ledger

    parser = argparse.ArgumentParser(description="Log paper-trade candidates. PAPER ONLY.")
    parser.add_argument("--ledger", default="data/paper/ledger.jsonl")
    parser.add_argument("--segments", default="data/paper/segments.json")
    parser.add_argument("--sites-root", default=None)
    parser.add_argument("--sport", action="append", default=None,
                        help="limit to one sport (default: every sport with a collector)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be written")
    args = parser.parse_args(argv)

    root = args.sites_root or sources.discover_sites_root()
    sports = args.sport or sorted(sources.SITE_FOR_SPORT)
    if root is None:
        print("no sibling site checkouts found; pass --sites-root", file=sys.stderr)
        return 2

    # A collector that is not there must stop the run. Otherwise the ledger
    # logs the sports whose collectors ARE there and silently omits the ones
    # whose are not, and every count it reports is then wrong in a way nobody
    # can see.
    try:
        sources.check_collectors(root, sports)
    except sources.MissingCollector as exc:
        print(f"missing Kalshi collector: {exc}", file=sys.stderr)
        return 3

    config = segments_mod.load(args.segments)
    ledger = Ledger(args.ledger)

    rows, games, positions = [], {}, {}
    for sport in sports:
        rows.extend(sources.read_line_files(sources.lines_files(Path(root), sport)))
        snapshot = sources.read_snapshot(Path(root) / sources.SITE_FOR_SPORT[sport] / "data" / "public_snapshot.json")
        games[sport] = sources.snapshot_games(snapshot, sport)
        positions.update(sources.snapshot_positions(snapshot))

    observed: list[dict] = []
    made, skipped = candidate_rows(rows, games, config,
                                   committed_by_game=ledger.committed_by_game(),
                                   positions=positions, scores=observed)
    scored = score_records(observed)
    known = {r["id"] for r in ledger.scores()}
    fresh_scores = [r for r in scored if r["id"] not in known]
    fresh = made if args.dry_run else ledger.append_new(made)
    if not args.dry_run:
        ledger.append_new(fresh_scores)

    counts = {"candidates": sum(1 for r in fresh if r["kind"] == "candidate"),
              "shadows": sum(1 for r in fresh if r["kind"] == "shadow")}
    print(json.dumps({
        "read_rows": len(rows), "written": len(fresh), **counts,
        "scores": len(fresh_scores),
        "skipped": dict(sorted(skipped.items())),
        "dry_run": bool(args.dry_run),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

