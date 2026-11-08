"""Capture a near-kickoff closing price for every open paper bet. PAPER ONLY.

The site collectors snapshot Kalshi twice a day, so a snapshot strictly after a
row was logged and strictly before its kickoff almost never exists and CLV
stays unmeasured. This polls Kalshi's PUBLIC read-only market endpoint (GET, no
credential, no auth header) for every open row kicking off within the next
WINDOW, and appends one observation per ticker per run to closing.jsonl. The
report merges those with the site snapshots (see `stats.closing_prices`).

There is no order code path here and no POST.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import sources
from .ledger import STATUS_OPEN, Ledger, resolved

API = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"
WINDOW = timedelta(minutes=45)
MAX_TICKERS = 120
RETRIES = 4
SOURCE = "kalshi-public"
DEFAULT_PATH = "data/paper/closing.jsonl"


def parse_market(body: str) -> dict | None:
    """Buyable prices from a market response, derived as the ladder rows are.

    The endpoint reports `*_dollars` strings; they are mapped onto the ladder
    row shape so `sources.buyable_price` applies its own rules (no quote, a
    0/1 side, a crossed book -> None).
    """
    try:
        market = json.loads(body)["market"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(market, dict):
        return None
    row = {}
    for side in ("yes", "no"):
        for leg in ("ask", "bid"):
            try:
                row[f"{side}_{leg}"] = float(market[f"{side}_{leg}_dollars"])
            except (KeyError, TypeError, ValueError):
                pass
    yes, no = sources.buyable_price(row, "yes"), sources.buyable_price(row, "no")
    if yes is None and no is None:
        return None
    return {"yes_price": yes, "no_price": no}


def fetch(ticker: str, *, get, sleep=time.sleep) -> dict | None:
    """One ticker's prices, retrying 429/5xx/network errors with backoff."""
    for attempt in range(RETRIES):
        status, body = get(API.format(ticker=ticker))
        if status == 200:
            return parse_market(body)
        if status == 429 or status >= 500 or status == 0:
            sleep(2 ** attempt)
            continue
        return None  # 404 and friends: not retryable
    return None


def due_tickers(rows: list[dict], now: datetime) -> dict[str, datetime]:
    """Open rows' tickers that kick off within WINDOW and have not started."""
    out: dict[str, datetime] = {}
    for row in rows:
        if row.get("status") != STATUS_OPEN or not isinstance(row.get("ticker"), str):
            continue
        start = sources.parse_ts(row.get("kickoff"))
        if start is not None and now < start <= now + WINDOW:
            out[row["ticker"]] = start
    return out


def read_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn line must not stop the report
    return out


def observation_rows(observations: list[dict], rows: list[dict]) -> list[dict]:
    """closing.jsonl observations shaped like `stats.annotate_observed` output.

    The kickoff comes from the ledger (the observation does not carry one).
    """
    kickoff = {}
    for row in rows:
        start = sources.parse_ts(row.get("kickoff"))
        if start is not None and isinstance(row.get("ticker"), str):
            kickoff[row["ticker"]] = start
    out = []
    for obs in observations:
        out.append({
            "ticker": obs.get("ticker"),
            "_seen_at": sources.parse_ts(obs.get("observed_at")),
            "_kickoff": kickoff.get(obs.get("ticker")),
            "_price": obs.get("yes_price"),
            "_no_price": obs.get("no_price"),
        })
    return out


def run(ledger_path: str, out_path: str, *, get, now: datetime | None = None,
        sleep=time.sleep) -> int:
    """Returns the number of observations appended."""
    now = now or datetime.now(timezone.utc)
    due = due_tickers(resolved(Ledger(ledger_path)), now)
    if not due:
        print("nothing to do")
        return 0
    stamp = now.replace(microsecond=0).isoformat()
    path = Path(out_path)
    seen = {(o.get("ticker"), o.get("observed_at")) for o in read_observations(path)}
    new = []
    for ticker in sorted(due, key=due.get)[:MAX_TICKERS]:
        if (ticker, stamp) in seen:
            continue
        try:
            prices = fetch(ticker, get=get, sleep=sleep)
        except Exception as exc:  # one ticker must never stop the rest
            print(f"{ticker}: {exc!r}", file=sys.stderr)
            continue
        if prices is None:
            print(f"{ticker}: no usable price", file=sys.stderr)
            continue
        new.append({"ticker": ticker, "observed_at": stamp, **prices, "source": SOURCE})
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for obs in new:
                handle.write(json.dumps(obs, sort_keys=True) + "\n")
    print(f"{len(due)} due, {len(new)} observed")
    return len(new)


def main(get, argv: list[str] | None = None) -> int:
    """`get(url) -> (status, body)` is supplied by the entry script, which owns the network."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default="data/paper/ledger.jsonl")
    parser.add_argument("--out", default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    run(args.ledger, args.out, get=get)
    return 0
