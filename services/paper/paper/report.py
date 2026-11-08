"""The segment report: does any market actually have an edge after fees?

Prints one row per segment and writes the same numbers to `data/paper/report.json`.

The gate, exactly as the brief states it. A segment may be called "has an edge"
only when ALL of:

  * at least 300 settled paper bets in that segment,
  * a paired bootstrap confidence interval on mean pnl whose LOWER bound is above
    zero — not merely one that excludes zero, since a segment which lost all 300
    of its bets also excludes zero, and
  * a calibration gap that is not widening.

None of the three can be waived, and there is no override flag. The first run
will almost certainly print "no" for everything, and that is the expected and
correct result: the interesting claim is the one the data refuses to support,
and a report that says so is the product working.

CLOSING-LINE VALUE is reported next to pnl because pnl is noisy and CLV is not:
CLV asks whether we found the price before the market did, which is the part of
the edge that should persist. Positive pnl with negative CLV is luck.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import closing as closing_mod
from . import segments as segments_mod
from . import sources, stats
from .segments import MIN_SETTLED_GAMES_FLOOR
from .ledger import (KIND_CANDIDATE, KIND_SHADOW, STATUS_SETTLED, Ledger, resolved,
                     resolved_scores)

REPORT_PATH = Path("data/paper/report.json")


def _won(row: dict) -> bool:
    return row.get("outcome") == "yes"


# The bucket a row with no game id shares with the other such rows of its
# segment. It is not a game id, and it never counts as a game.
NO_GAME_ID = "\x00no_game_id:"


def game_id_of(row: dict) -> str:
    """The row's game id, or "" when it has none.

    `game_id` is the site's own game identifier for NFL, NBA, CFB and PL, and
    the RACE for F1 (whose contracts are one per driver per race).
    """
    return str(row.get("game_id") or "").strip()


def cluster_key(row: dict) -> str:
    """The key a ledger row's evidence is clustered by: one game, one race.

    A row WITH a game id clusters on it. A row WITHOUT one must NOT fall back
    to its ticker: a ladder's rung tickers are unique per rung -- a 12-rung CFB
    spread game holds 12 distinct tickers -- so a ticker fallback would make
    every rung its own "game". That inflates the game count and narrows the
    interval, which is the exact bug the clustering exists to fix. It is
    fail-open, and it was fail-open here until a reviewer caught it.

    So rows with no game id share ONE bucket per segment instead. They count
    as no games at all, and the report says how many rows they were.
    """
    gid = game_id_of(row)
    if gid:
        return gid
    return NO_GAME_ID + str(row.get("segment") or "")


def summarise(rows: list[dict], *, closing: dict[str, dict], gate_cfg: dict,
              force_no: str | None = None, is_current_model: bool = True) -> dict:
    """Every number the report prints for one (segment, model_fingerprint)."""
    settled = [r for r in rows if r.get("status") == STATUS_SETTLED]
    outcomes = [1 if _won(r) else 0 for r in settled]

    pnls = [float(r.get("pnl_per_contract") or 0.0) for r in settled]
    stakes = [float(r.get("stake") or 0.0) for r in settled]
    staked = sum(stakes)

    realised_edge = None
    if settled:
        realised_edge = round((sum(outcomes) / len(outcomes)
                               - stats.fmean(r["model_p"] for r in settled)) * 100.0, 4)

    # The evidence is GAMES, not bets. A Kalshi ladder is many contracts on one
    # game -- a CFB game carries a moneyline, a spread ladder and a total ladder
    # -- so the live ledger holds 239 CFB candidates from 44 games. Counting every
    # rung as an independent bet lets 300 bets be about twenty-five games of
    # evidence, and resampling bets treats thirteen rungs of one game as
    # thirteen independent observations, which makes the interval far too
    # narrow.
    #
    settled_games = {game_id_of(r) for r in settled} - {""}
    all_games = {game_id_of(r) for r in rows} - {""}
    n_settled_no_game_id = sum(1 for r in settled if not game_id_of(r))

    # The interval is on the mean pnl PER GAME, which is a different quantity
    # from the old per-bet interval and a larger number.
    ci = stats.bootstrap_mean_ci_clustered(
        [(cluster_key(r), float(r.get("pnl_per_contract") or 0.0)) for r in settled]
    )
    trend = stats.calibration_trend(settled, min_n=gate_cfg["min_for_calibration_trend"])
    # CLV is a LEADING indicator, so it is measured over EVERY logged row --
    # candidates and shadows, settled and open -- not only the settled ones.
    # An open row's close is known as soon as the market's last pre-kickoff
    # price is, which is before the game; hiding that until settlement would
    # throw away the one thing CLV is for.
    #
    # It is deliberately NOT one of the gate's conditions. CLV says whether the
    # entry was early; it does not say whether the bet won, and a segment is
    # never called "has an edge" on the strength of it.
    clv_pairs = [(cluster_key(r), v) for r in rows
                 if (v := stats.clv_points(r, closing)) is not None]
    clvs = [v for _, v in clv_pairs]

    fingerprint = rows[0].get("model_fingerprint") if rows else None

    reasons: list[str] = []
    if force_no:
        reasons.append(force_no)
    if not is_current_model:
        reasons.append("superseded by a newer model; a fresh sample is required")
    if len(settled) < gate_cfg["min_settled"]:
        reasons.append(f"only {len(settled)} settled bets, need {gate_cfg['min_settled']}")
    min_settled_games = int(gate_cfg.get("min_settled_games", MIN_SETTLED_GAMES_FLOOR))
    if len(settled_games) < min_settled_games:
        reasons.append(f"only {len(settled_games)} settled games, need {min_settled_games}")
    if n_settled_no_game_id:
        # A row that cannot be attributed to a game is evidence we cannot
        # count, and the interval lumps it into one bucket, so the counts and
        # the interval are not describing the same population. Fail closed.
        reasons.append(f"{n_settled_no_game_id} settled rows have no game id, "
                       "so their games are not counted")
    if gate_cfg["require_ci_excludes_zero"]:
        if ci is None:
            reasons.append("no settled bets to put an interval on")
        elif not stats.is_profitable(ci):
            verdict = ("includes zero" if stats.excludes_zero(ci) is False
                       else "is entirely below zero")
            reasons.append(f"pnl interval [{ci[0]:.4f}, {ci[1]:.4f}] {verdict}")
    if gate_cfg["require_calibration_not_widening"]:
        if not trend["measurable"]:
            reasons.append(f"calibration trend needs {trend['min_n']} settled bets, have {trend['n_settled']}")
        elif trend["widening"]:
            reasons.append(f"calibration gap widening ({trend['gap_early']:.2f} -> {trend['gap_late']:.2f} pts)")

    return {
        "segment": rows[0].get("segment") if rows else None,
        "sport": rows[0].get("sport") if rows else None,
        "model_fingerprint": fingerprint,
        "is_current_model": is_current_model,
        "n_logged": len(rows),
        "n_settled": len(settled),
        "n_open": len(rows) - len(settled),
        "n_void": sum(1 for r in rows if r.get("status") == "void"),
        # The evidence, in both units. A segment's bets are not independent of
        # each other -- the rungs of one game move together -- so the number of
        # GAMES is what says how much there is to know.
        "n_bets": len(rows),
        "n_games": len(all_games),
        "n_settled_bets": len(settled),
        "n_settled_games": len(settled_games),
        # Rows that cannot be attributed to a game. They are counted as no
        # games, and the gate says so.
        "n_rows_no_game_id": sum(1 for r in rows if not game_id_of(r)),
        "n_settled_no_game_id": n_settled_no_game_id,
        "hit_rate": round(sum(outcomes) / len(outcomes), 4) if outcomes else None,
        "mean_model_p": round(stats.fmean(r["model_p"] for r in settled), 4) if settled else None,
        "mean_market_price": round(stats.fmean(r["market_price"] for r in settled), 4) if settled else None,
        "mean_edge_points": round(stats.fmean(r["edge"] for r in settled), 4) if settled else None,
        "realised_edge_points": realised_edge,
        "mean_clv_cents": round(stats.fmean(clvs), 4) if clvs else None,
        "n_clv": len(clvs),
        # The share of rows that beat the close. A segment where most rows beat
        # the close is one where the model got in before the market; a segment
        # where few do is one where the entry was late.
        "share_clv_positive": round(sum(1 for v in clvs if v > 0) / len(clvs), 4) if clvs else None,
        # The raw values, so the per-sport roll-up can combine segments without
        # re-deriving them. Dropped before the report is written: it is an
        # intermediate, not a figure a reader should see.
        "_clv_values": clvs,
        "_clv_pairs": clv_pairs,
        **stats.clv_summary(clv_pairs),
        "calibration_gap_points": (round(trend["gap_late"], 4) if trend["measurable"]
                                   else stats.calibration_gap(
                                       [r["model_p"] for r in settled], outcomes)),
        "calibration_trend": trend,
        "pnl": round(sum(pnls), 4),
        "staked": round(staked, 4),
        "roi": round(sum(pnls) / staked, 4) if staked else None,
        "pnl_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "max_drawdown": round(stats.max_drawdown(pnls), 4),
        "qualifies_for_edge_claim": not reasons,
        "disqualifying": reasons,
    }


def _is_legacy_total(row: dict) -> bool:
    """A total row logged before totals were priced at each rung's own line.

    Those rows carry the snapshot's single book-line over_prob for every rung, so
    their edges are fiction. They stay in the append-only ledger but are not evidence.
    """
    return row.get("market") == "total" and not row.get("model_basis")


def _collapse_reasons(reasons: list[str]) -> list[str]:
    """The same reason said once, however many segment groups it applies to.

    Two reasons that differ only in their numbers are one reason: "only 5
    settled bets, need 300" and "only 3 settled bets, need 300" are the same
    problem in two segments. The first of each shape is kept.
    """
    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        shape = _reason_shape(reason)
        if shape in seen:
            continue
        seen.add(shape)
        out.append(reason)
    return out


def _reason_shape(reason: str) -> str:
    """A reason with its numbers removed, so two of them can be compared."""
    import re
    return re.sub(r"-?\d+(?:\.\d+)?", "N", reason)


def _why_not_yet(reasons: list[str], *, n_settled: int, min_settled: int,
                 n_settled_games: int, min_settled_games: int,
                 has_shadow_only: bool) -> str | None:
    """The reason a sport cannot be judged yet, in plain words, or None.

    The gate's own wording is a list of technical phrases written for the person
    reading the source. The page has to say the same thing to a reader who does
    not know what a "segment group" is, so each count shortfall is stated as a
    number still needed and the other conditions are named.

    "needs 295 more settled bets" is both plain and specific. "only 5 settled
    bets, need 300" is neither as plain nor as useful.

    BOTH floors are named when both are short, and each is named in its own
    unit. A reader who sees "needs 97 more settled games" learns something a
    reader of "only 3 settled games, need 100" does not: that the evidence is
    spread too thinly over games, which is a different problem from having too
    few bets.
    """
    if not reasons:
        return None
    parts: list[str] = []
    shortfall = max(0, min_settled - n_settled)
    if shortfall:
        parts.append(f"needs {shortfall} more settled bets (has {n_settled}, needs {min_settled})")
    game_shortfall = max(0, min_settled_games - n_settled_games)
    if game_shortfall:
        parts.append(f"needs {game_shortfall} more settled games (has {n_settled_games}, "
                     f"needs {min_settled_games}); the bets are spread over too few games "
                     "to be independent evidence")
    if has_shadow_only:
        parts.append("its rows are shadows, which carry no stake and cannot support an edge claim")
    for reason in reasons:
        # The counts and the shadow reason are already said above, in plainer
        # words; saying them twice reads as two separate problems.
        if "settled bets" in reason:
            continue
        if "settled games" in reason:
            continue
        if has_shadow_only and "shadow rows carry no stake" in reason:
            continue
        parts.append(reason)
    return "; ".join(parts) if parts else "; ".join(reasons)


def _per_sport(rows: list[dict], table: dict[str, dict], gate_cfg: dict,
               legacy_by_sport: dict[str, int]) -> dict[str, dict]:
    """One roll-up per sport, for the page's per-sport rows.

    The per-segment table groups by (segment, model fingerprint), which is what
    the gate is applied to. A reader wants to know how a SPORT is doing, and the
    page needs a sport filter, so the same evidence is rolled up one level up
    here rather than being re-derived in the browser.

    The roll-up is a summary of the segment groups, not a second gate. Whether a
    sport qualifies is the AND of its segment groups' verdicts, so a sport can
    never qualify that one of its segments does not.
    """
    by_sport: dict[str, list[dict]] = {}
    for row in rows:
        sport = row.get("sport")
        if sport:
            by_sport.setdefault(str(sport), []).append(row)
    # The legacy rows are excluded from the evidence ABOVE, so they are not in
    # `rows` by the time this runs; their count is passed in so the sport can
    # still say that some of its rows were set aside.

    out: dict[str, dict] = {}
    for sport, sport_rows in sorted(by_sport.items()):
        settled = [r for r in sport_rows if r.get("status") == STATUS_SETTLED]
        pnls = [float(r.get("pnl_per_contract") or 0.0) for r in settled]
        stakes = [float(r.get("stake") or 0.0) for r in settled]
        staked = sum(stakes)
        # Clustered, for the same reason the per-segment interval is: the rungs
        # of one game are not independent observations.
        ci = stats.bootstrap_mean_ci_clustered(
            [(cluster_key(r), float(r.get("pnl_per_contract") or 0.0)) for r in settled]
        )
        settled_games = {game_id_of(r) for r in settled} - {""}
        all_games = {game_id_of(r) for r in sport_rows} - {""}
        n_settled_no_game_id = sum(1 for r in settled if not game_id_of(r))
        clvs = []
        clv_pairs = []
        for key, s in table.items():
            if not key.startswith(f"{sport}:"):
                continue
            clvs.extend([v for v in (s.get("_clv_values") or []) if v is not None])
            clv_pairs.extend(s.get("_clv_pairs") or [])
        qualifies = [s for s in table.values() if s.get("sport") == sport]
        # A sport qualifies only if EVERY one of its segment groups qualifies.
        # "Any" would let one good segment carry a sport past the gate that its
        # other segments have not cleared.
        sport_qualifies = bool(qualifies) and all(
            s.get("qualifies_for_edge_claim") for s in qualifies)
        min_settled_games = int(gate_cfg.get("min_settled_games", MIN_SETTLED_GAMES_FLOOR))
        reasons: list[str] = []
        for s in qualifies:
            reasons.extend(s.get("disqualifying") or [])
        # Each segment group has its own reasons, and the same reason often
        # applies to all of them ("only 5 settled bets, need 300"). Saying it
        # once per group reads as several separate problems, so the reasons are
        # grouped by their shape: the ones that are the same apart from the
        # numbers are said once.
        unique_reasons = _collapse_reasons(reasons)
        out[sport] = {
            "sport": sport,
            "n_logged": len(sport_rows),
            "n_candidates": sum(1 for r in sport_rows if r.get("kind") != KIND_SHADOW),
            "n_shadow": sum(1 for r in sport_rows if r.get("kind") == KIND_SHADOW),
            "n_settled": len(settled),
            "n_open": len(sport_rows) - len(settled),
            # Both units: a sport's bets are not independent of each other, so
            # the number of GAMES is what says how much there is to know.
            "n_bets": len(sport_rows),
            "n_games": len(all_games),
            "n_settled_bets": len(settled),
            "n_settled_games": len(settled_games),
            "n_rows_no_game_id": sum(1 for r in sport_rows if not game_id_of(r)),
            "n_settled_no_game_id": n_settled_no_game_id,
            "segments": len({r.get("segment") for r in sport_rows if r.get("segment")}),
            "segment_groups": len(qualifies),
            "pnl": round(sum(pnls), 4),
            "staked": round(staked, 4),
            "roi": round(sum(pnls) / staked, 4) if staked else None,
            "pnl_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "mean_clv_cents": round(stats.fmean(clvs), 4) if clvs else None,
            "n_clv": len(clvs),
            **stats.clv_summary(clv_pairs),
            "share_clv_positive": (round(sum(1 for v in clvs if v > 0) / len(clvs), 4)
                                    if clvs else None),
            "excluded_legacy_total_rows": legacy_by_sport.get(sport, 0),
            "qualifies_for_edge_claim": sport_qualifies,
            "has_edge_claim": sport_qualifies,
            "why_not_yet": (_why_not_yet(
                unique_reasons,
                n_settled=len(settled),
                min_settled=gate_cfg["min_settled"],
                n_settled_games=len(settled_games),
                min_settled_games=min_settled_games,
                has_shadow_only=bool(sport_rows) and all(r.get("kind") == KIND_SHADOW
                                                        for r in sport_rows),
            ) if not sport_qualifies else None),
        }
    return out


def _total_clv(table: dict) -> dict:
    pairs = [p for s in table.values() for p in (s.get("_clv_pairs") or [])]
    vals = [v for _, v in pairs]
    return {
        "n_clv": len(vals),
        "mean_clv_cents": round(stats.fmean(vals), 4) if vals else None,
        "share_clv_positive": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
        **stats.clv_summary(pairs),
    }



# ---------------------------------------------------------------------------
# Q1 scoring and the sport-level verdict (additive; the segment gate is above).
# ---------------------------------------------------------------------------
SCORING_DOCS = {
    "population": "Settled, non-void rows with model_p and market_price. Score rows (one per game+market, "
                  "no stake, earliest pre-kickoff observation) first; candidate and shadow rows are a "
                  "backfill only for (game_id, market) pairs that have no score row, deduped by "
                  "(game_id, market, ticker), earliest logged_at wins. Backfill rows were selected "
                  "for a >= 4 point model-over-market edge, so they flatter the model: read "
                  "n_from_backfill as an upper bound on model quality until score rows dominate.",
    "clipping": "model_p and market_price are clipped to [0.01, 0.99] before log-loss and Brier.",
    "market_price": "The buyable ask (not the mid).",
    "n_games": "Distinct game_id (race for F1). n_from_score_rows / n_from_backfill count ROWS.",
    "paired_logloss_diff": "Mean over games of (market log-loss - model log-loss), each game averaging its "
                           "rows; positive = model better. 95% percentile bootstrap over games, "
                           "2000 resamples, fixed seed.",
    "p_model_better": "Share of bootstrap draws with the paired difference > 0.",
    "games_to_resolve": "More games needed for the CI half-width to fall below 0.005 log-loss at the "
                        "observed SD (iid approximation); null under 10 games.",
}


def _fp_sport(rows: list[dict]) -> dict[str, str | None]:
    """Each sport's current model fingerprint: the one logged most recently."""
    latest: dict[str, tuple[str, str | None]] = {}
    for r in rows:
        sport = r.get("sport")
        if sport and (sport not in latest or (r.get("logged_at") or "") >= latest[sport][0]):
            latest[sport] = (r.get("logged_at") or "", r.get("model_fingerprint"))
    return {k: v[1] for k, v in latest.items()}


def score_points(scores: list[dict], others: list[dict]) -> list[dict]:
    """The Q1 population: settled score rows, plus candidate/shadow backfill (see SCORING_DOCS)."""
    def ok(r):
        return (r.get("outcome") in ("yes", "no") and game_id_of(r)
                and isinstance(r.get("model_p"), (int, float))
                and isinstance(r.get("market_price"), (int, float)))

    def point(r, source):
        return {"sport": r.get("sport"), "game_id": game_id_of(r), "market": r.get("market"),
                "ticker": r.get("ticker"), "model_p": float(r["model_p"]),
                "market_price": float(r["market_price"]), "y": 1 if r["outcome"] == "yes" else 0,
                "source": source, "model_fingerprint": r.get("model_fingerprint"),
                "logged_at": r.get("logged_at") or ""}

    covered = {(r.get("sport"), game_id_of(r), r.get("market")) for r in scores}
    out = [point(r, "score") for r in scores if ok(r)]
    seen: dict[tuple, dict] = {}
    for r in sorted(others, key=lambda r: r.get("logged_at") or ""):
        if _is_legacy_total(r) or not ok(r):
            continue
        if (r.get("sport"), game_id_of(r), r.get("market")) in covered:
            continue
        seen.setdefault((r.get("sport"), game_id_of(r), r.get("market"), r.get("ticker")), r)
    return out + [point(r, "backfill") for r in seen.values()]


def build_scoring(points: list[dict]) -> dict:
    sports = sorted({p["sport"] for p in points if p["sport"]})
    keyed = [dict(p, game_id=f"{p['sport']}|{p['game_id']}") for p in points]
    return {"docs": SCORING_DOCS,
            "per_sport": {s: stats.score_summary([p for p in keyed if p["sport"] == s]) for s in sports},
            "total": stats.score_summary(keyed)}


def _verdict(pnl_rows: list[dict], points: list[dict], *, min_bets: int, min_events: int) -> dict:
    """One sport-level verdict from settled staked rows (Q3) and the Q1 population."""
    groups: dict = {}
    for r in pnl_rows:
        groups.setdefault(f"{r['sport']}|{game_id_of(r)}", []).append(float(r.get("pnl_per_contract") or 0.0))
    n = len(pnl_rows)
    c = stats.cluster_ci(groups, by_row=True)
    mean = c["mean"] if c else None
    sd = None
    needed = None
    if n >= 2:
        vals = [v for g in groups.values() for v in g]
        sd = (sum((v - mean) ** 2 for v in vals) / (n - 1)) ** 0.5
        if mean > 0:
            needed = max(0, math.ceil((1.96 * sd / mean) ** 2 - 1e-9) - n)
    trend = stats.calibration_trend(sorted(pnl_rows, key=lambda r: r.get("logged_at") or ""),
                                    min_n=min_bets)
    q1 = stats.score_summary(points)
    ll_ci = q1["paired_logloss_diff_ci95"]
    pnl_floors = n >= min_bets and len(groups) >= min_events
    q1_floors = q1["n_games"] >= min_events
    pnl_lo, pnl_hi = (c["ci"] if c else (None, None))
    reasons = []
    if not pnl_floors:
        reasons.append(f"{n} settled bets over {len(groups)} games; need {min_bets} bets and {min_events} games")
    if not q1_floors:
        reasons.append(f"{q1['n_games']} scored games; need {min_events} for the Q1 claim")

    if (pnl_floors and pnl_hi < 0) or (q1_floors and ll_ci[1] < 0):
        status = "against"
        if pnl_floors and pnl_hi < 0:
            reasons.append("P&L interval is entirely below zero")
        if q1_floors and ll_ci[1] < 0:
            reasons.append("paired log-loss interval is entirely on the market-better side")
    elif not pnl_floors:
        status = "not_yet"
    elif (pnl_lo > 0 and q1_floors and ll_ci[0] > 0 and trend["measurable"] and not trend["widening"]):
        status = "proven"
    elif mean > 0 and (not q1_floors or q1["paired_logloss_diff"] > 0):
        status = "leaning"
    else:
        status = "not_yet"
        reasons.append("floors met but the point estimate is not favourable and no interval is entirely against")
    if status in ("leaning", "not_yet") and pnl_floors and c and pnl_lo <= 0:
        reasons.append("P&L interval includes zero")
    if status != "proven" and q1_floors and ll_ci[0] <= 0 <= ll_ci[1]:
        reasons.append("paired log-loss interval includes zero")
    if pnl_floors and trend["measurable"] and trend["widening"]:
        reasons.append("calibration gap widening")
    r4 = lambda x: None if x is None else round(x, 4)
    return {
        "status": status, "reasons": reasons,
        "n_settled_bets": n, "n_events": len(groups),
        "min_settled_bets": min_bets, "min_events": min_events,
        "pnl_per_contract": r4(mean), "pnl_per_contract_ci95": None if not c else [r4(pnl_lo), r4(pnl_hi)],
        "pnl_sd_per_contract": r4(sd),
        "needed_bets_at_observed_effect": needed,
        "calibration_trend": trend,
        "q1_paired_logloss_diff": q1["paired_logloss_diff"],
        "q1_paired_logloss_diff_ci95": ll_ci,
        "q1_n_games": q1["n_games"],
        "q1_p_model_better": q1["p_model_better"],
    }


def build_verdicts(rows: list[dict], points: list[dict], scores: list[dict], sport_cfg: dict) -> dict:
    """Per-sport and pooled verdicts, all-time and current-model-only. Pre-stated config; never tuned."""
    bets = [r for r in rows if r.get("kind") != KIND_SHADOW and r.get("status") == STATUS_SETTLED
            and game_id_of(r) and not _is_legacy_total(r)]
    current = _fp_sport(rows + scores)
    cur = lambda r: r.get("model_fingerprint") == current.get(r.get("sport"))
    events = sport_cfg["min_events"]
    default = events["default"]
    sports = sorted({r["sport"] for r in bets + points if r.get("sport")})

    def both(sport, min_events):
        sel = (lambda r: r.get("sport") == sport) if sport else (lambda r: True)
        out = {}
        for name, extra in (("all_time", lambda r: True), ("current_model_only", cur)):
            out[name] = _verdict([r for r in bets if sel(r) and extra(r)],
                                 [p for p in points if sel(p) and extra(p)],
                                 min_bets=sport_cfg["min_settled_bets"], min_events=min_events)
        return out

    return {"config": sport_cfg,
            "per_sport": {s: both(s, events.get(s, default)) for s in sports},
            "pooled": both(None, default)}


def build(rows: list[dict], *, closing: dict[str, dict], gate_cfg: dict,
          score_rows: list[dict] | None = None, sport_gate_cfg: dict | None = None) -> dict:
    n_legacy = sum(1 for r in rows if _is_legacy_total(r))
    # How many of the set-aside rows each sport had, so a sport can say so.
    legacy_by_sport: dict[str, int] = {}
    for r in rows:
        if _is_legacy_total(r) and r.get("sport"):
            legacy_by_sport[str(r["sport"])] = legacy_by_sport.get(str(r["sport"]), 0) + 1
    rows = [r for r in rows if not _is_legacy_total(r)]
    # Group by (segment, model_fingerprint) so an updated model starts a fresh
    # sample. The gate applies to each group independently.
    by_group: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row.get("segment"), row.get("model_fingerprint"))
        by_group.setdefault(key, []).append(row)

    # Which fingerprint is "current" for each segment: the one whose most recent
    # logged_at is the latest. A model that has been superseded cannot qualify,
    # even if its own 300 bets looked good.
    current_fp: dict[str, str | None] = {}
    for (segment, fp), bucket in by_group.items():
        if segment is None:
            continue
        latest = max((r.get("logged_at") or "") for r in bucket)
        held = current_fp.get(segment)
        held_latest = None
        if held is not None:
            held_latest = max((r.get("logged_at") or "") for r in by_group.get((segment, held), []))
        if held is None or latest > held_latest:
            current_fp[segment] = fp

    table: dict[str, dict] = {}
    for (segment, fp), bucket in sorted(by_group.items()):
        if segment is None:
            continue
        is_current = (fp == current_fp.get(segment))
        key = f"{segment} :: {fp if fp is not None else 'unknown'}"
        table[key] = summarise(bucket, closing=closing, gate_cfg=gate_cfg,
                               force_no=None if any(r.get("kind") != KIND_SHADOW for r in bucket)
                               else "shadow rows carry no stake and cannot support an edge claim",
                               is_current_model=is_current)

    # Per-segment roll-up: current-model n and all-time n, for the summary line.
    per_segment: dict[str, dict] = {}
    for (segment, fp), bucket in by_group.items():
        if segment is None:
            continue
        agg = per_segment.setdefault(segment, {"all_time_n": 0, "current_model_n": 0,
                                              "fingerprints": set()})
        agg["all_time_n"] += len(bucket)
        agg["fingerprints"].add(fp)
        if fp == current_fp.get(segment):
            agg["current_model_n"] += len(bucket)
    for agg in per_segment.values():
        agg["fingerprints"] = sorted(agg["fingerprints"], key=lambda x: (x is None, str(x)))

    per_sport = _per_sport(rows, table, gate_cfg, legacy_by_sport)

    scores = list(score_rows or [])
    points = score_points(scores, rows)
    sport_cfg = sport_gate_cfg or segments_mod.sport_gate({})

    return {
        "scoring": build_scoring(points),
        "verdicts": build_verdicts(rows, points, scores, sport_cfg),
        "schema_version": 1,
        "gate": gate_cfg,
        "totals": {
            "n_logged": len(rows),
            "n_settled": sum(1 for r in rows if r.get("status") == STATUS_SETTLED),
            "n_candidates": sum(1 for r in rows if r.get("kind") != KIND_SHADOW),
            "n_shadow": sum(1 for r in rows if r.get("kind") == KIND_SHADOW),
            "segments": len({r.get("segment") for r in rows if r.get("segment")}),
            "segment_groups": len(table),
            "segments_qualifying": sum(1 for v in table.values() if v["qualifies_for_edge_claim"]),
            "excluded_legacy_total_rows": n_legacy,
            **_total_clv(table),
        },
        "per_segment": per_segment,
        "per_sport": per_sport,
        "segments": table,
    }


def render(report: dict) -> str:
    """The printed table. Fixed width, no dependency on a table library."""
    header = (f"{'segment :: fingerprint':<40} {'n':>5} {'games':>6} {'settled':>7} {'hit':>6} {'model_p':>8} {'price':>6} "
              f"{'edge':>7} {'real.':>7} {'clv':>7} {'clv+':>6} {'pnl':>9} {'roi':>7} {'dd':>7} {'edge?':>6}")
    lines = [
        "paper-trading segment report — PAPER ONLY, no orders, no credentials",
        f"gate: >= {report['gate']['min_settled']} settled bets AND >= "
        f"{report['gate']['min_settled_games']} settled games AND bootstrap CI on pnl "
        f"entirely above zero AND calibration gap not widening",
        "the pnl interval is a CLUSTER bootstrap: it resamples games, not bets, because "
        "the rungs of one game are not independent (its interval is per game)",
        "clv is a leading indicator, not a gate condition: clv+ is the share of "
        "logged rows that beat the market's last pre-kickoff price",
        "",
        header,
        "-" * len(header),
    ]

    def num(value, fmt="{:.4f}", width=None):
        return "—" if value is None else fmt.format(value)

    for name, s in sorted(report["segments"].items()):
        lines.append(
            f"{name:<40} {s['n_logged']:>5} {s['n_games']:>6} {s['n_settled']:>7} {num(s['hit_rate'], '{:.3f}'):>6} "
            f"{num(s['mean_model_p']):>8} {num(s['mean_market_price']):>6} "
            f"{num(s['mean_edge_points'], '{:.2f}'):>7} {num(s['realised_edge_points'], '{:.2f}'):>7} "
            f"{num(s['mean_clv_cents'], '{:+.2f}'):>7} {num(s.get('share_clv_positive'), '{:.2f}'):>6} "
            f"{s['pnl']:>9.2f} {num(s['roi'], '{:+.3f}'):>7} "
            f"{s['max_drawdown']:>7.3f} {('yes' if s['qualifies_for_edge_claim'] else 'no'):>6}"
        )
    if not report["segments"]:
        lines.append("(no segments: the ledger is empty)")
    totals = report["totals"]
    lines += [
        "",
        f"{totals['n_logged']} rows logged ({totals['n_candidates']} candidates, "
        f"{totals['n_shadow']} shadow), {totals['n_settled']} settled, "
        f"{totals['segments_qualifying']}/{totals['segment_groups']} segment-groups qualify for an edge claim.",
    ]
    if totals.get("excluded_legacy_total_rows"):
        lines.append(f"{totals['excluded_legacy_total_rows']} earlier total rows excluded (priced at the wrong line).")
    # Current-model vs all-time n per segment.
    per_segment = report.get("per_segment") or {}
    if per_segment:
        lines.append("")
        lines.append("per segment: current-model n / all-time n")
        for seg, agg in sorted(per_segment.items()):
            lines.append(f"  {seg}: {agg['current_model_n']} / {agg['all_time_n']}  "
                         f"({len(agg['fingerprints'])} model fingerprint(s))")
    for name, s in sorted(report["segments"].items()):
        if s["disqualifying"]:
            lines.append(f"  {name}: " + "; ".join(s["disqualifying"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default="data/paper/ledger.jsonl")
    parser.add_argument("--segments", default="data/paper/segments.json")
    parser.add_argument("--sites-root", default=None,
                        help="directory holding the sibling site checkouts")
    parser.add_argument("--lines", nargs="*", default=None,
                        help="Kalshi line .jsonl.gz files to read closing prices from")
    parser.add_argument("--closing", default=closing_mod.DEFAULT_PATH,
                        help="near-kickoff observations from scripts/paper_close.py")
    parser.add_argument("--out", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    config = segments_mod.load(args.segments)
    ledger = Ledger(args.ledger)
    rows = resolved(ledger)

    files = [Path(p) for p in args.lines] if args.lines else []
    if not files:
        from .sources import discover_sites_root, SITE_FOR_SPORT
        root = args.sites_root or discover_sites_root()
        if root:
            for sport in sorted(set(SITE_FOR_SPORT.values())):
                files.extend(sources.lines_files(root, sport))
    obs = closing_mod.read_observations(Path(args.closing))
    closing = stats.closing_prices(
        stats.annotate_observed(sources.read_line_files(files))
        + closing_mod.observation_rows(obs, rows))

    report = build(rows, closing=closing, gate_cfg=segments_mod.gate(config),
                   score_rows=resolved_scores(ledger), sport_gate_cfg=segments_mod.sport_gate(config))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_public(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render(report))
    print(f"\nwrote {out}")
    return 0


def _public(report: dict) -> dict:
    """The report as it is written: without the internal intermediates.

    The segment summaries carry `_clv_values` so the per-sport roll-up can
    combine them. That is a working value, not a figure, and the file the page
    reads should carry only figures.
    """
    out = dict(report)
    out["segments"] = {k: {sk: sv for sk, sv in v.items() if not sk.startswith("_")}
                       for k, v in (report.get("segments") or {}).items()}
    return out


if __name__ == "__main__":
    sys.exit(main())
