"""The append-only paper ledger: `data/paper/ledger.jsonl`.

One JSON object per line. The file is only ever opened for append, and no record
that has been written is ever rewritten or removed. Everything else in this
package depends on that, so it is worth being explicit about why.

The temptation with a ledger is to update a row in place — mark it settled, fill
in the pnl, correct the price. Every one of those edits is a chance to improve a
row using information that did not exist when the bet was logged, which is
exactly the hindsight the paper ledger exists to rule out. So:

  * A candidate is written ONCE, at the first observation at which it qualifies.
    The `id` is a hash of what was bet, not of when it was seen, so running the
    logger twice over the same market produces the same id and the second run
    writes nothing. Idempotency is a consequence of the id, not a separate check.
  * Settlement is a NEW record that references the candidate's id. The candidate
    line keeps `status: "open"` forever, on disk, as the claim that was made at
    the time. Readers fold the settle record on top; the file never does.
  * A later price is a `price_update` record. It cannot change the price the
    candidate was logged at, which is what makes closing-line value a real
    measurement instead of a restatement.

Void and push are recorded the same way — as a settle record whose outcome is
"void" — so a voided market still leaves its candidate line untouched on disk.

PAPER ONLY. There is no order-placement or credential code in this package, and
none may be added: a paper ledger that could place a real order could not be
run honestly against real data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LEDGER_PATH = Path("data/paper/ledger.jsonl")

# Sizing. All paper, all in units of a notional 1,000-unit bankroll.
BANKROLL_UNITS = 1000.0
KELLY_FRACTION = 0.25
MAX_BET_FRACTION = 0.01   # 10 units on any one bet
MAX_GAME_FRACTION = 0.02  # 20 units on one game, however many bets ride on it

STATUS_OPEN = "open"
STATUS_SETTLED = "settled"
STATUS_VOID = "void"

KIND_CANDIDATE = "candidate"
KIND_SHADOW = "shadow"

#: A no-stake scoring observation (Q1: model vs market over EVERY priced game).
#: Its own record type, so it can never be counted as a bet, candidate or P&L.
RECORD_SCORE = "score"


def score_id(*, sport: str, game_id: str, market: str, ticker: str | None = None) -> str:
    """Stable id of a score row: one per (game, market); F1 adds the driver's ticker.

    Salted with "score" so it can never collide with a candidate id and so a
    settle record (keyed by id) can reference either kind unambiguously.
    """
    parts = ["score", sport, game_id, market, ticker or ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def candidate_id(*, sport: str, game_id: str, market: str, selection: str | None,
                 side: str, line: float | None) -> str:
    """A stable id for "this bet", independent of when it was seen.

    The brief specifies the hash inputs as sport / game / market / side / line.
    Those five are not a key on their own: an NBA moneyline rung has no player
    and no line, so `KXNBAGAME-26OCT21SACLAC-SAC` (Sacamento yes) and
    `...-LAC` (Lakers yes) would hash identically, and the second one would be
    silently swallowed as a duplicate. `selection` is therefore part of the key.
    It is the field the brief calls `player` for a prop and carries the Kalshi
    team code for a game market.

    What is deliberately NOT in the key: the price, the snapshot, the model
    probability and the clock. Two observations of the same bet at two different
    prices are one bet, and the first one is the one that counts.
    """
    parts = [sport, game_id, market, selection or "", side,
             "" if line is None else f"{float(line):g}"]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def kelly_units(model_p: float, price: float, *, bankroll: float = BANKROLL_UNITS,
                fraction: float = KELLY_FRACTION) -> float:
    """Fractional-Kelly stake in units, for a $1-notional contract bought at `price`.

    Winning pays 1 - price, losing costs price, so full Kelly on this contract is
    (model_p - price) / (1 - price). A negative edge is a negative Kelly, which
    means do not bet: this returns 0 for it, because a stake is a decision to
    take the risk and a negative one is not a small bet but a short position.
    """
    if not (0.0 <= price < 1.0):
        return 0.0
    edge = model_p - price
    if edge <= 0.0:
        return 0.0
    return max(0.0, fraction * (edge / (1.0 - price)) * bankroll)


def stake_for(model_p: float, price: float, *, game_id: str,
              committed_by_game: dict[str, float] | None = None,
              bankroll: float = BANKROLL_UNITS) -> float:
    """The stake for one bet: fractional Kelly, then the per-bet cap, then the game cap.

    The per-game cap is the correlated-exposure rule. Several bets can ride on
    one game — both sides of a spread, a moneyline and a total — and they are not
    three independent risks, so they are allowed to spend `MAX_GAME_FRACTION`
    between them. `committed_by_game` carries what the ledger has already
    committed to that game across earlier runs, so the cap holds over the life
    of the ledger rather than resetting every time the logger runs.

    Passing the running total in is what puts the cap in one place instead of at
    every call site: the only caller that can be wrong is the one that forgets
    to thread it through, and it fails by over-committing a game, which the next
    run then also sees.
    """
    raw = kelly_units(model_p, price, bankroll=bankroll)
    capped = min(raw, MAX_BET_FRACTION * bankroll)
    already = (committed_by_game or {}).get(game_id, 0.0)
    room = MAX_GAME_FRACTION * bankroll - already
    return round(max(0.0, min(capped, room)), 4)


class Ledger:
    """An append-only JSONL file. Reading it is free; the only write is `append`."""

    def __init__(self, path: Path | str = LEDGER_PATH):
        self.path = Path(path)

    def records(self) -> list[dict]:
        """Every record in the file, in the order it was written."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out

    def candidate_ids(self) -> set[str]:
        return {r["id"] for r in self.records()
                if r.get("record") == KIND_CANDIDATE and r.get("id")}

    def candidates(self) -> list[dict]:
        return [r for r in self.records() if r.get("record") == KIND_CANDIDATE]

    def settles(self) -> dict[str, dict]:
        """The last settle record per id. Settling twice is not an error."""
        out: dict[str, dict] = {}
        for row in self.records():
            if row.get("record") == "settle" and row.get("id"):
                out[row["id"]] = row
        return out

    def scores(self) -> list[dict]:
        """Score rows (no stake, never bets). A separate population from candidates()."""
        return [r for r in self.records() if r.get("record") == RECORD_SCORE]

    def price_updates(self) -> list[dict]:
        return [r for r in self.records() if r.get("record") == "price_update"]

    def committed_by_game(self, *, settled_ids: set[str] | None = None) -> dict[str, float]:
        """Units already at risk per game, counting open rows only.

        A settled row's stake is spent money, not exposure, so it stops counting
        against the game cap. The per-bet cap still applies to it, which is why
        this sums stakes rather than removing settled rows from the file.
        """
        settled = self.settles() if settled_ids is None else settled_ids
        out: dict[str, float] = {}
        for row in self.candidates():
            if row["id"] in settled:
                continue
            game_id = row.get("game_id")
            if game_id:
                out[game_id] = out.get(game_id, 0.0) + float(row.get("stake") or 0.0)
        return out

    def append(self, record: dict) -> None:
        """Append one record. This is the only way anything gets written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def append_new(self, records: list[dict]) -> list[dict]:
        """Append only the candidates whose id is not already in the file.

        Everything already present is skipped, including a candidate whose id is
        on file with different fields — a settled row, or one logged earlier at
        another price. First write wins, always.
        """
        seen = self.candidate_ids() | {r["id"] for r in self.scores() if r.get("id")}
        fresh = []
        for record in records:
            if record.get("record") not in (KIND_CANDIDATE, RECORD_SCORE):
                self.append(record)
                continue
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            self.append(record)
            fresh.append(record)
        return fresh


def resolved_scores(ledger: Ledger) -> list[dict]:
    """Score rows with their settle record folded on, as new dicts (outcome yes/no/void)."""
    settles = ledger.settles()
    out = []
    for row in ledger.scores():
        merged = dict(row)
        settle = settles.get(row.get("id"))
        if settle:
            merged["status"] = settle.get("status", STATUS_SETTLED)
            merged["outcome"] = settle.get("outcome")
            merged["settled_at"] = settle.get("settled_at")
        out.append(merged)
    return out


def resolved(ledger: Ledger) -> list[dict]:
    """Candidates with their settle record folded on, as new dicts.

    Returns copies. The point of the fold being a copy is that no caller can
    reach through it and mutate what is on disk, which is the failure this whole
    module exists to make impossible.
    """
    settles = ledger.settles()
    out = []
    for row in ledger.candidates():
        merged = dict(row)
        settle = settles.get(row.get("id"))
        if settle:
            merged["status"] = settle.get("status", STATUS_SETTLED)
            merged["outcome"] = settle.get("outcome")
            merged["pnl_per_contract"] = settle.get("pnl_per_contract")
            merged["settled_at"] = settle.get("settled_at")
        out.append(merged)
    return out
