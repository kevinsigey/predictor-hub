# services/paper — the paper-trading ledger

**Paper only.** No order placement, no credentials, no network. Everything here
reads public files the sibling site repos commit and writes two files of its own.
`tests/test_gate.py` asserts the absence of order and credential code in CI,
because a ledger that could place a real order could not honestly be run against
real data.

## What it is for

Record every bet the models *would* have made, at the price that was actually
available at the time, settle it from the real result, and report whether any
**segment** — sport × market × confidence bucket — has an edge over the market
after fees. A segment may only be called "has an edge" on evidence, never on a
flag.

## Running it

```bash
# The site checkouts live as siblings of the hub. SITES_ROOT overrides discovery.
export SITES_ROOT=/path/to/the/dir/holding/NFL_Predictor

python3 scripts/paper_log.py       # append newly qualifying candidates
python3 scripts/paper_settle.py    # settle open rows from Kalshi's result
python3 scripts/paper_report.py    # print the segment table, write report.json

cd services/paper && PYTHONPATH=. python3 -m pytest -q
```

`paper_log.py` and `paper_settle.py` take `--dry-run`. Nothing writes on a dry run.

## The three rules everything else serves

1. **Append-only.** `data/paper/ledger.jsonl` is only ever opened for append. A
   candidate is written once, at the first observation that qualifies; settlement
   is a separate record referencing the same id; a later price is a
   `price_update`. No record is ever rewritten, because every rewrite is a chance
   to improve a row using information that did not exist when the bet was logged.
   See `paper/ledger.py`.

2. **No hindsight.** Four guards, each with a test in `test_no_hindsight.py`: the
   row's own `snapshot_ts` is the `logged_at` (never the wall clock); the price
   must be strictly before kickoff, read from `occurrence` and not `close_time`
   (Kalshi keeps these markets open for days afterwards); the logged price comes
   from that same row and no other; and the model's `generated_at` must be at or
   before the price's `snapshot_ts`, or the row is a **shadow** — logged,
   countable, never staked.

3. **Buyable prices only.** YES is logged at `yes_ask`, NO at `no_ask`. NFL's own
   `kalshi_lines.implied_probability` returns the bid/ask *midpoint*, and a
   midpoint is not a price anybody pays. A dead quote (0 or 1 cent) or a crossed
   book means there was no price to log.

**Depth.** Each row records `ask_size`, the contracts resting at the bought
side's best ask in the same snapshot (the collectors' `yes_ask_size` /
`no_ask_size`, from Kalshi's public, unauthenticated market list), and
`contracts`, the size wanted (stake / price; a shadow is sized as its own bet).
`fill_short` is true when `ask_size < contracts`. The report keeps fill_short
rows out of every figure and shows them in `fills` with the fill rate. Rows
from snapshots without a size have `fill_short: null` (unknown, kept).

Fees are a **verbatim copy** of `shared/kalshi_fees.py` from `algo-trade-hub-prod`
(commit `12432be`), with its mutation-grade pin tests. Do not reimplement them: it
rounds the total order fee up once, after applying the contract count, and a
per-contract-rounded copy would disagree with every number that repo has already
published.

## Where the numbers come from

| | source |
|---|---|
| price, timestamp, result | `data/lines/*.jsonl.gz` in `NFL_Predictor` / `NBA_Predictor` / `F1_Predictor` — self-settling, no box-score join |
| model probability | `data/public_snapshot.json` in the same repos |
| allow-list and gate | `data/paper/segments.json` |

## Current state, and why

**The ledger is empty and no segment qualifies.** That is the correct output, not a
placeholder. The two halves of the input do not currently meet:

| | per-game probability published? | Kalshi game price collected? | candidates |
|---|---|---|---|
| NFL | yes (`public_snapshot.json`, per-game) | **no** — the collector fetches four player-prop series and no `KXNFLGAME` | none |
| NBA | **no** — only aggregate `manifest.metrics` | yes (`KXNBAGAME`, `KXNBASPREAD`, `KXNBATOTAL`) | none |
| CFB | — | no collector exists at all | none |
| F1 | yes (per driver, per race) | **no** — the F1 site has no Kalshi collector yet | none |

Against the real committed data the logger reads ~19,500 rows and reports every
skip reason by count, so the gap is visible rather than silent. Injecting a
per-game probability for the real NBA games and nothing else produces 19 correctly
sized candidates, which is how the pipeline was verified.

Player props (WR/TE receptions, QB passing TDs) are a separate change in the NFL
repo, not a hub one, and what is missing there is a calibrated probability per
rung rather than a model at all: the site's committed `public_snapshot.json`
already carries a `receptions_p_over` for 269 of week 6's 415 player rows and a
`passing_td_over_prob` for 46, and its `market-lines` endpoint already attaches a
`model_p` to the receptions and passing-TD rungs it can match, but that number
is the probability at the model's own line (`receptions_line_source` is
`model_line`) copied onto every rung of the ladder, which is the same defect
`paper/log.py::p_over_line` calls out for totals, and the rows behind it carry
no `generated_at` (the endpoint falls back to the request time), no model
version and no game, so nothing today can price a rung at that rung's own line
or prove the probability predates the price it would be paired with. For
`nfl:receptions` and `nfl:passing_tds` to become loggable candidates, the NFL
repo would have to publish, in the committed snapshot this ledger already reads,
one row per counted prop carrying the game the prop belongs to, a player id
the ledger can match to a rung (the Kalshi player token, `NETHENDERSON32` in
`KXNFLREC-26OCT22NECHI-NETHENDERSON32-4`, since the two sides spell the
player's name differently), the rung's own line, the probability that the
player reaches that line as a number in [0,1] and not a projection of the
stat, the timestamp the probability was made, and the model version, so
results can be grouped by model. Receptions prices are already collected (the
21 October `data/lines` file holds 2,524 `KXNFLREC` rows), but `KXNFLPASSTDS`
is not in the collector's series list, so QB passing TDs have no collected
price either; until both halves land, both segments stay blocked and can only
ever be shadows.

`segments.json` is the place to change the pre-stated 4-point edge threshold, and
`tests/test_gate.py` fails if the 300-bet floor is ever lowered. A gate that can
be lowered by an edit is not a gate.

## The evidence is games, not bets

A Kalshi ladder is many contracts on one game. A CFB game carries a moneyline,
a spread ladder of a dozen rungs and a total ladder of a dozen more; the live
ledger holds **239 CFB candidates from 44 games**, and the F1 contracts are one
per driver per race, so six of them are one race of evidence. So the count of
bets is not the count of evidence: three quarters of the CFB spread segment's
bets are the same game said again at a different price.

Three things follow, and all three are in the report:

- **Every segment reports `n_games` next to `n_bets`.** `game_id` is the cluster
  key everywhere: the site's own game identifier for NFL, NBA, CFB and PL, and
  the *race* for F1.

  A row with **no** `game_id` does not fall back to its ticker. A ladder's rung
  tickers are unique per rung — a 12-rung CFB spread game holds 12 distinct
  tickers — so a ticker fallback makes every rung its own "game", which
  *inflates* the game count and *narrows* the interval: the same bug, in the
  opposite direction. Rows with no game id instead share one bucket per segment,
  count as **no games at all**, and the gate says how many rows they were.
- **The pnl interval is a CLUSTER bootstrap.** Each game's rungs are summed
  into one value and the GAMES are resampled. Resampling bets instead would
  treat thirteen rungs of one game as thirteen independent observations, which
  is how a segment gets a narrow interval out of a handful of games. The
  interval is therefore on the mean pnl **per game**, which is a different and
  larger number than the old per-bet one.
- **A second floor, `min_settled_games: 100`, has to be met alongside the
  300-bet floor.** Both are required; neither replaces the other. The
  300-bet floor is untouched, and `tests/test_gate.py` fails if the game floor
  is lowered below 100 either.

The floor is a number of GAMES because a game is the unit the world supplies:
the game's outcome is one draw, however many contracts the ledger held on it.

## F1

**F1 is not a game market.** Kalshi lists one binary contract per *driver* per
race — "Main Race: Max Verstappen wins" — so the probability that belongs to a
rung is that driver's own probability for that race, and the ledger looks it up
by (race, driver) rather than by home/away. `paper/sources.py` holds the F1
tables; `paper/log.py::_f1_candidate` does the lookup.

### Which Kalshi F1 series exist

Checked against Kalshi's public API on 2026-10-21: 30 `KXF1*` series are
active, and 15 of them have open markets. "Priceable" means the F1 site's
published snapshot contains a probability for that contract.

| series | Kalshi's title | open markets | priceable from the snapshot? |
|---|---|---|---|
| `KXF1RACE` | F1 Race — "Main Race: *driver* wins" | 22 | **yes** — `p_win` |
| `KXF1RACEPODIUM` | F1 Race Podium Finisher — "to finish top 3" | 22 | **yes** — `p_podium` (P(position ≤ 3)) |
| `KXF1TOP10` | F1 Top 10 Finishers — "to finish top 10" | 22 | **yes** — `p_points_finish`, which is P(finish in the points) and the model's points table ends at P10 |
| `KXF1POLE` | F1 Pole Position — "*driver* is awarded Pole Position" | 22 | **yes** — `p_pole`, from the qualifying session's prediction |
| `KXF1` | Formula 1 (Drivers Championship) — "Will *driver* win the F1 Drivers Championship?" | 22 | **yes** — `championship.drivers.standings[].win_prob` |
| `KXF1TOP5` | F1 Top 5 Finishers — "to finish top 5" | 22 | no — the model publishes no `p_top_5` |
| `KXF1FASTLAP` | F1 Fastest Lap — "Main Race Fastest Lap: *driver*" | 22 | no — the model publishes no fastest-lap probability |
| `KXF1TOPCONSTRUCTOR` | F1 Top Constructor — "Main Race Top Constructor: *team*" | 11 | no — the model publishes no per-constructor race probability |
| `KXF1CONSTRUCTORS` | Formula 1 Constructors — "Will *team* win the F1 Constructors Championship?" | 11 | no — a per-constructor championship probability exists, but the snapshot's constructor ids do not map onto Kalshi's constructor names without a hand-maintained table (`rb` vs "Racing Bulls") |
| `KXF1RETIRE` | F1 Retire — "Will *driver* announce his retirement before 20XX?" | 4 | no — a 2027-2030 announcement question, not a race |
| `KXF1OCCUR` | Formula 1 Event Occurrence — "Will the *GP* main race take place?" | 1 | no — about the event, not a driver |
| `KXF1ACTION` | FIA Action of the Year award | 23 | no |
| `KXF1BIGGESTMOVER` | F1 Biggest Mover | 22 | no |
| `KXF1CHINA` | F1 team from China | 1 | no |
| `KXF1NEXTTEAM` | F1 Next Team | 12 | no |
| `KXF1H2H`, `KXF1SPRINTFASTLAP`, `KXF1SPRINTTOP5`, `KXF1SPRINTTOP10`, `KXF1SPRINTTOPCONSTRUCTOR` | head-to-head and sprint variants, settled | 0 | no matching probability — there is no head-to-head, fastest-lap, top-5 or per-constructor race probability, and the sprint's points cut is P8, so `p_points_finish` is not Kalshi's sprint "top 10" |
| `KXF1RACESPRINT`, `KXF1SPRINTPOLE` | the sprint race and the sprint qualifying pole, settled | 0 | the model could price them (`p_win` from the sprint session, `p_pole` from the sprint-qualifying session), but nothing is traded today, so they are not wired |
| `KXF1QUALIFY`, `KXF1DELAY`, `KXF1FASTPIT`, `KXF1FASTESTLAP`, `KXF1POLEPOSITION`, `KXF1RACETOP5`, `KXF1RACETOP10`, `KXF1RACETOPX` | never opened — Kalshi has no market for these at all | 0 | — |

A series with no open markets is not a contract that cannot be priced; it is a
contract that is not being traded right now. They are listed so the reader can
see the whole F1 surface. `KXF1POLEPOSITION` and `KXF1RACETOP10` would price
the same contracts as `KXF1POLE` and `KXF1TOP10`; either way there is nothing
to log today, so none of them is wired.

### The three rules F1 adds

1. **A prediction the model published after the session is history.**
   `source` is `live` (computed now, for an upcoming session) or `tracked` (the
   tracking store recorded it *before* the session, which is why
   `honest_source` may demote it to `rebuilt`). `backtest` and `rebuilt` are
   rejected: a backtest is a replay computed once the race was over, and a
   rebuild is a stored row the tracking store cannot vouch for.
2. **The tier has to fit the contract.** A race contract is about the race, so
   every race tier (`pre_weekend`, `post_practice`, `post_qualifying`) is
   forward-looking for it. A pole contract is about the *qualifying session*,
   so `post_qualifying` — the tier the model publishes once that session has
   started — is hindsight for it, and is rejected.
3. **The cutoff is the earlier of two times.** Kalshi's F1 `occurrence_datetime`
   runs about **six hours later** than the session it names. This is not a
   guess: for the Singapore, Dutch and Azerbaijan races Kalshi's own
   `close_time` (the moment it finalised the market) is published *before* its
   stated occurrence, which cannot be true of a race start, and the offset is
   the same six hours on every race and qualifying session checked. The
   snapshot knows the real session time, so the ledger uses the earlier of the
   two — a price seen after the real start is not logged, however Kalshi's own
   field reads.

### How a Kalshi event ticker finds its race

Kalshi's F1 event ticker is `<SERIES>-<GP CODE><YY>` (`KXF1RACE-UNIS26`) or
`<SERIES>-<YY>` for a season-long contract (`KXF1-26`). The grand-prix code is
**not** used: it is respelled every season (2025's `AGP25` is 2026's
`AUSGP26`), so it carries nothing the snapshot does not already know and a
table of them would have to be rewritten every January. What does not change is
*when*: the event's own occurrence is the session the contract is about, and the
snapshot says when each of its sessions starts. The nearest session within 48
hours is the one, and nothing within 48 hours means the row is not priced.
`tests/test_f1.py` pins this against a real Kalshi event ticker and the
snapshot's own race list.

The driver is found the same way: Kalshi titles the contract with the driver's
full name ("Main Race: Max Verstappen wins") and the snapshot keys it by a slug
(`max_verstappen`), and every word of the slug appears in the title. So the
match needs no maintained table either — the slug's words must all appear in
the title as whole words, and exactly one driver may match. Ambiguity and
silence both log nothing, because a contract priced with the wrong driver's
probability is worse than no row at all.

### F1's own confidence bands

A race is spread over two dozen drivers, so the model's best race-winner
probability for a round is around 14% and its best podium probability around
38%. The 0.50+ bands that suit a two-sided game would exclude every F1
contract there is, so `segments.json` gives F1 its own bands
(`confidence_buckets_by_sport`). Every other sport uses the bands it had, so
no existing row changes; the bands are not the gate and the gate is untouched.

### The honest state of F1 here

**There are no F1 price rows yet.** The F1 site has no Kalshi collector — no
`kalshi_lines` module and no `collect-kalshi-lines.yml` workflow — so
`F1_Predictor/data/lines/` does not exist and the F1 segments log nothing.
That is the whole story: the hub side is wired and tested against fixtures,
and the input it needs has not been collected.

The collector is a separate PR in the `F1_Predictor` repo. It has to write the
same row shape the NFL and NBA collectors write (`snapshot_ts`, `series`,
`ticker`, `title`, `event_ticker`, `yes_bid`/`yes_ask`/`no_bid`/`no_ask`,
`volume`, `open_interest`, `status`, `result`, `occurrence`, `close_time`), and
the `title` is not optional: it is what the driver is read from.

