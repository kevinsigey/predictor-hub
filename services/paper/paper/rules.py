"""Preregistered shadow rules: written down before their data, judged only on it.

A rule is a JSON spec in `data/paper/rules/<id>.json`. It names what it bets,
the exact threshold and metric, `prereg_at` (UTC), the promotion test and the
kill limits, all before a single row it will be judged on exists. Then:

  * only ledger rows DECIDED after `prereg_at` count -- a row decided earlier is
    the data the idea came from, not evidence for it;
  * a rule is killed at its kill limit (60 days or 300 trades, whichever comes
    first) unless it is eligible by then. A kill is appended to
    `data/paper/rules/kills.jsonl` and is terminal: a killed id never reports
    anything but killed again, whatever its later trades look like;
  * shadows never change live behaviour. Nothing in `log.py`, `settle.py` or the
    segment gate reads this module; the report only says "eligible", and
    promotion to live needs Kevin's approval.

The only population implemented is the one the first rule needs: the ledger's
score rows (one per game and market, the earliest proven pre-start observation
of the YES rung). Its trade is the model's side at that side's own ask, held to
settlement, net of the Kalshi taker fee.

PAPER ONLY.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import sources
from .fees import kalshi_fee_cents
from .ledger import is_replay

RULES_DIR = Path("data/paper/rules")
KILLS_FILE = "kills.jsonl"

#: The kill limits a spec may not loosen.
MAX_DAYS = 60
MAX_TRADES = 300

REQUIRED = ("id", "description", "population", "sport", "market", "parameters", "metric",
            "prereg_at", "promotion", "kill")


def validate(spec: dict) -> dict:
    """The spec, or ValueError. A rule that cannot be read is not a rule."""
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise ValueError(f"rule {spec.get('id')!r} is missing {missing}")
    if spec["population"] != "score":
        raise ValueError(f"rule {spec['id']}: unknown population {spec['population']!r}")
    stamp = sources.parse_ts(spec["prereg_at"])
    if stamp is None or not spec["prereg_at"].endswith(("Z", "+00:00")):
        raise ValueError(f"rule {spec['id']}: prereg_at must be a UTC timestamp")
    kill = spec["kill"]
    if not (0 < kill.get("max_days", 0) <= MAX_DAYS and 0 < kill.get("max_trades", 0) <= MAX_TRADES):
        raise ValueError(f"rule {spec['id']}: kill limits may not exceed {MAX_DAYS} days / {MAX_TRADES} trades")
    return spec


def load(directory: Path | str = RULES_DIR) -> tuple[list[dict], list[dict]]:
    """(validated specs, kill records) from the rules directory."""
    directory = Path(directory)
    if not directory.is_dir():
        return [], []
    specs = [validate(json.loads(p.read_text(encoding="utf-8")))
             for p in sorted(directory.glob("*.json"))]
    kills_path = directory / KILLS_FILE
    kills = [json.loads(line) for line in kills_path.read_text(encoding="utf-8").splitlines()
             if line.strip()] if kills_path.exists() else []
    return specs, kills


def append_kills(records: list[dict], directory: Path | str = RULES_DIR) -> None:
    """Append-only, like the ledger."""
    if not records:
        return
    path = Path(directory) / KILLS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def trades(spec: dict, score_rows: list[dict]) -> list[dict]:
    """The rule's trades: one per qualifying score row decided after prereg_at."""
    prm = spec["parameters"]
    prereg = sources.parse_ts(spec["prereg_at"])
    out = []
    for row in score_rows:
        if row.get("sport") != spec["sport"] or row.get("market") != spec["market"] or is_replay(row):
            continue
        decided = sources.parse_ts(row.get("price_observed_at") or row.get("logged_at"))
        start = sources.parse_ts(row.get("kickoff"))
        if decided is None or start is None or decided <= prereg:
            continue
        if (start - decided).total_seconds() < 3600.0 * prm["min_lead_hours"]:
            continue
        ask, mid, p = row.get("market_price"), row.get("market_mid"), row.get("model_p")
        if not all(isinstance(v, (int, float)) for v in (ask, mid, p)):
            continue
        bid = round(2.0 * mid - ask, 4)
        if ask - bid > prm["max_spread_points"] / 100.0:
            continue
        d = p - mid
        if abs(d) < prm["min_disagreement"]:
            continue
        # The model's side, at that side's own ask: Kalshi's NO ask is 1 - YES bid.
        side = "yes" if d > 0 else "no"
        price = ask if side == "yes" else round(1.0 - bid, 4)
        if not (0.01 < price < 0.99):
            continue
        fee = kalshi_fee_cents(price * 100.0, contracts=1) / 100.0
        outcome = row.get("outcome")
        pnl = None
        if outcome in ("yes", "no"):
            pnl = round((1.0 if outcome == side else 0.0) - price - fee, 6)
        out.append({"rule_id": spec["id"], "game_id": row.get("game_id"), "ticker": row.get("ticker"),
                    "side": side, "price": price, "fee": fee, "disagreement": round(d, 6),
                    "decided_at": decided.isoformat(), "outcome": outcome, "pnl_per_contract": pnl})
    return out


def evaluate(specs: list[dict], kills: list[dict], score_rows: list[dict],
             incumbent: list[dict], *, now: datetime, promotion_test) -> tuple[dict, list[dict]]:
    """({rule id: summary}, new kill records). `promotion_test` is report's three legs."""
    killed = {k["rule_id"]: k for k in kills}
    out, new_kills = {}, []
    for spec in specs:
        made = trades(spec, score_rows)
        settled = [t for t in made if t["pnl_per_contract"] is not None]
        games = {t["game_id"] for t in made}
        inc = [r for r in incumbent if r.get("sport") == spec["sport"] and r.get("market") == spec["market"]
               and r.get("game_id") in games and isinstance(r.get("pnl_per_contract"), (int, float))]
        promo = promotion_test(settled, inc, min_n=spec["promotion"]["min_n"])
        days = (now - sources.parse_ts(spec["prereg_at"])).total_seconds() / 86400.0
        kill = killed.get(spec["id"])
        if kill is None and not promo["eligible"] and (
                days >= spec["kill"]["max_days"] or len(made) >= spec["kill"]["max_trades"]):
            kill = {"record": "rule_kill", "rule_id": spec["id"], "killed_at": now.isoformat(),
                    "reason": "kill limit reached without passing the promotion test",
                    "n_trades": len(made), "days": round(days, 2)}
            new_kills.append(kill)
            killed[spec["id"]] = kill
        status = "killed" if kill else ("eligible" if promo["eligible"] else "collecting")
        out[spec["id"]] = {
            "id": spec["id"], "description": spec["description"], "sport": spec["sport"],
            "market": spec["market"], "prereg_at": spec["prereg_at"], "status": status,
            "kill": kill, "kill_limits": spec["kill"], "days_since_prereg": round(max(days, 0.0), 2),
            "n_trades": len(made), "n_settled": len(settled), "n_games": len(games),
            "n_yes": sum(1 for t in made if t["side"] == "yes"),
            "n_no": sum(1 for t in made if t["side"] == "no"),
            # Score rows carry no size, so a shadow's fill rate is not measured.
            "fill_rate": None,
            "promotion": promo,
        }
    return out, new_kills
