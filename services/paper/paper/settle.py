"""Settle open rows from Kalshi's own resolution, idempotently.

Settlement here is a lookup, not a computation. Every Kalshi rung we logged came
from a row that carries `status` and `result`, and Kalshi is the authority on how
its market resolved. So a ledger row settles by matching its `ticker` to the
finalized Kalshi row for the same ticker and reading off what happened. There is
no scoreboard join, no box-score fetch, and no independent opinion about who
won.

That inheritance is the reason the four awkward cases are handled explicitly
rather than left to the arithmetic:

  win        result == "yes"  -> payout 1, pnl = (1 - price - fee)
  loss       result == "no"   -> payout 0, pnl = -(price + fee)
  push/tie   a finalized market whose result is neither yes nor no. Kalshi
             resolves its own ties (a spread landing exactly on the number is
             settled by Kalshi's published rule, not by us), so a push does not
             arrive as a distinct value -- it arrives as a finalized market with a
             non-binary result. It is treated as a VOID: the contract expires
             worthless and pnl is zero. The stake is returned; no fee is charged,
             because no contract traded.
  voided /   likewise: cancelled, postponed-then-abandoned, or expired unsettled.
  postponed  A postponed game is NOT void. Its rung simply stays un-finalized, so
             the row stays open, which is the honest state: we do not yet know.
             Only a final Kalshi resolution ends it.

Idempotent: a row with a settle record is skipped, and settling twice writes
nothing. Settlement is also append-only — the original candidate line is never
touched (see `ledger`).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from . import sources
from .ledger import STATUS_OPEN, STATUS_SETTLED, STATUS_VOID

# Kalshi market states that mean "this is over, the result field is authoritative".
FINAL_STATES = frozenset({"finalized", "closed", "settled"})

# Result values that are not a win for either side. These are the push and the
# void, and both are worth zero to the contract holder.
NON_BINARY_RESULTS = frozenset({"", "push", "tie", "void", "voided", "cancelled",
                                "canceled", "abandoned", "postponed"})


def pnl_per_contract(outcome: str, price: float, fee: float) -> float:
    """Profit on one $1-notional contract, fee already included in `fee`."""
    if outcome == "void":
        return 0.0
    payout = 1.0 if outcome == "yes" else 0.0
    return round(payout - price - fee, 6)


def resolution(row: dict) -> tuple[str, str] | None:
    """(status, outcome) for a Kalshi line row, or None if it has not resolved.

    Fails closed: anything unrecognised stays open rather than being settled on a
    guess. A row that says `status: "finalized"` but carries a result this module
    does not recognise is a void, not a loss — settling it as a loss would book a
    loss we cannot justify.
    """
    status = row.get("status")
    if not isinstance(status, str) or status.lower() not in FINAL_STATES:
        return None
    result = row.get("result")
    result = result.strip().lower() if isinstance(result, str) else ""
    if result == "yes":
        return STATUS_SETTLED, "yes"
    if result == "no":
        return STATUS_SETTLED, "no"
    if result in NON_BINARY_RESULTS or result == "":
        return STATUS_VOID, "void"
    return STATUS_VOID, "void"  # unrecognised result: void, never a loss


def finalised_by_ticker(rows: list[dict]) -> dict[str, dict]:
    """Latest resolved Kalshi row per ticker.

    Later files win, so a market that was `active` in yesterday's snapshot and
    `finalized` in today's resolves to the finalized row.
    """
    out: dict[str, dict] = {}
    for row in rows:
        ticker, resolved = row.get("ticker"), resolution(row)
        if not isinstance(ticker, str) or resolved is None:
            continue
        previous = out.get(ticker)
        if previous is None:
            out[ticker] = row
            continue
        new_ts = sources.parse_ts(row.get("snapshot_ts"))
        old_ts = sources.parse_ts(previous.get("snapshot_ts"))
        if old_ts is None or (new_ts is not None and new_ts >= old_ts):
            out[ticker] = row
    return out


def settle(ledger, rows: list[dict], *, now: datetime | None = None) -> dict[str, int]:
    """Append settle records for every open row Kalshi has now resolved."""
    settled_now = ledger.settles()
    by_ticker = finalised_by_ticker(rows)
    stamped = (now or datetime.now(timezone.utc)).isoformat()

    counts = {"settled": 0, "void": 0, "already": 0, "unresolved": 0}
    for row in ledger.candidates():
        candidate_id = row.get("id")
        if candidate_id in settled_now:
            counts["already"] += 1
            continue
        if row.get("status") != STATUS_OPEN:
            counts["already"] += 1
            continue
        source = by_ticker.get(row.get("ticker"))
        if source is None:
            counts["unresolved"] += 1
            continue
        status, outcome = resolution(source)
        pnl = pnl_per_contract(outcome, float(row["market_price"]), float(row["fee"]))
        ledger.append({
            "record": "settle",
            "id": candidate_id,
            "ticker": row.get("ticker"),
            "settled_at": stamped,
            "status": status,
            "outcome": outcome,
            "pnl_per_contract": pnl,
            # The Kalshi row that decided it, so a settled number can always be
            # traced back to the observation that produced it.
            "source_snapshot_ts": source.get("snapshot_ts"),
            "source_result": source.get("result"),
        })
        settled_now[candidate_id] = status
        counts["void" if status == STATUS_VOID else "settled"] += 1

    # Score rows settle the same way, from Kalshi's own result. They carry no
    # stake, so there is no pnl; the outcome is all that is stored.
    scores = ledger.scores()
    if scores:
        counts["scores_settled"] = counts["scores_void"] = 0
    for row in scores:
        score_id = row.get("id")
        if score_id in settled_now:
            continue
        source = by_ticker.get(row.get("ticker"))
        if source is None:
            continue
        status, outcome = resolution(source)
        ledger.append({
            "record": "settle", "id": score_id, "ticker": row.get("ticker"),
            "settled_at": stamped, "status": status, "outcome": outcome,
            "pnl_per_contract": None,
            "source_snapshot_ts": source.get("snapshot_ts"),
            "source_result": source.get("result"),
        })
        settled_now[score_id] = status
        counts["scores_void" if status == STATUS_VOID else "scores_settled"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from .ledger import Ledger
    from .sources import (SITE_FOR_SPORT, MissingCollector, check_collectors,
                          discover_sites_root, lines_files, read_line_files)

    parser = argparse.ArgumentParser(description="Settle paper rows from Kalshi's result. PAPER ONLY.")
    parser.add_argument("--ledger", default="data/paper/ledger.jsonl")
    parser.add_argument("--sites-root", default=None)
    parser.add_argument("--lines", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = [Path(p) for p in args.lines] if args.lines else []
    if not files:
        root = args.sites_root or discover_sites_root()
        if root is None:
            print("no sibling site checkouts found; pass --sites-root or --lines", file=sys.stderr)
            return 2
        # A collector that is not there must stop the run. Settling the sports
        # whose collectors ARE there and silently skipping the ones whose are
        # not would leave rows open that should have settled, and the report
        # would say "unresolved" as if the market had not resolved.
        try:
            check_collectors(root)
        except MissingCollector as exc:
            print(f"missing Kalshi collector: {exc}", file=sys.stderr)
            return 3
        for sport in sorted(SITE_FOR_SPORT):
            files.extend(lines_files(root, sport))

    ledger = Ledger(args.ledger)
    rows = read_line_files(files)
    if args.dry_run:
        pending = [r for r in ledger.candidates() if r["id"] not in ledger.settles()]
        resolved_map = finalised_by_ticker(rows)
        print(json.dumps({
            "read_rows": len(rows), "open_rows": len(pending),
            "resolvable_now": sum(1 for r in pending if r.get("ticker") in resolved_map),
            "dry_run": True,
        }, indent=2))
        return 0

    counts = settle(ledger, rows)
    counts["read_rows"] = len(rows)
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
