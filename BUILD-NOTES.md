# Build notes — hub landing board (feat/home-landing)

The five static sport cards are replaced by the approved Kalshi-style
landing board: a curated grid of live market cards with a sport filter,
built in a VM clone for Kevin's review. **Nothing is pushed or deployed.**

## Files changed

- `index.html` — new landing shell. The family frame is untouched: the
  inlined predictor-ui tokens (drift-tested), the wordmark, the nav
  scroller and its CSS, the hero strip with `<h1>Sports Predictor</h1>`
  (kept — a deliberate brand pin in hub.test.mjs; the mockup's
  "Today's predictions" line is the board title beneath it), and the
  honesty `.rule` strap. The nav gains a leading **Home** tab
  (`href="/"`, `aria-current="page"`; Kevin's call, 2026-11-01). The old
  `.board`/`.sport`/`.teaser` markup and CSS are gone; new board CSS uses
  only the pinned tokens (accent via each card's `[data-sport]` override,
  6px radius, tabular numbers, nothing under 12px, no gradients/shadows).
  Two deliberate adaptations from the mockup: the deck header's accent
  rides its **top** edge (the family's convention — a thick coloured left
  edge is banned by the craft floor), and the mockup's px sizes became
  rem on the repo's scale.
- `home.js` — **new**, next to teasers.js, same conventions: pure
  exported selectors/builders (no DOM, no fetch), then a painting layer
  that builds cards with `textContent` only, then per-sport fetchers on
  isolated promise chains. Imports `pct`, `driverName`, `freshness` from
  teasers.js. Filter state lives in the URL hash (`#nfl`, …).
- `tests/home.test.mjs` — **new**: 11 tests over the builders, fed
  fixtures in the raw API shapes (probed live 2026-11-01, see below).
- `tests/hub.test.mjs` — updated where it pinned the OLD homepage
  structure (see below).
- `.github/workflows/tests.yml` — `tests/home.test.mjs` added to the CI
  test list.

## Endpoint map (all same-origin proxy paths, as teasers.js uses)

| Sport | Picks | Players | Record | Meta |
|---|---|---|---|---|
| PL | `/pl/api/fixtures/gameweek` | `/pl/api/fixtures/{event_id}/players` (first 2 upcoming) | `/pl/api/hub/track-record` | — (no route exists; 404 confirmed) |
| NFL | `/nfl/api/current-week` → `/nfl/api/games`, `/nfl/api/predictions/{s}/{w}/batch` | `/nfl/api/players/{s}/{w}/props` | `/nfl/api/track-record` | `/nfl/api/snapshot-meta` |
| CFB | same under `/cfb` | same under `/cfb` | `/cfb/api/track-record` | `/cfb/api/snapshot-meta` |
| NBA | `/nba/games/week?start={today}` | `/nba/games/{id}/players` (first 4 upcoming) | `/nba/hub/track-record` + `/nba/calibration` + `/nba/hub/rankings` | `/nba/snapshot-meta` |
| F1 | `/f1/api/races` → `/f1/api/races/{season}/{round}/prediction` | (drivers in the prediction) | `/f1/api/track-record` | `/f1/api/snapshot-meta` |

Field shapes were verified against the live APIs before coding, e.g.:
NBA predictions carry `home_win_probability` (not `home_win_prob`) and
the book spread as `market_point` in book sign (converted once in the
normalizer); PL track record is `{summary.by_market, pre_kickoff,
gameweeks[]}`; NFL/CFB records are `{games{…, pre_kickoff}, calibration}`;
NBA players arrive one row per player per stat
(`{player_name, team, stat, predicted_value, rebuilt}`) and are pivoted
on `player_id`. `/nfl/api/players/…/market-lines` is never called (it
times out); PL's calibration endpoint is never called (it returns
empty — the PL deck graphs the gameweek trend instead).

## Curation rules implemented

- **All view = predictions only.** Hero = most confident match pick
  across PL/NFL/CFB/NBA (wide, `.pr-notch`, BandChip computed from the
  probability), then the biggest model-vs-market gap **where a real
  line exists** (NBA totals/spreads from the games payload; football
  totals/spreads from the games payload's lines; card is skipped when
  no line exists anywhere), one marquee per sport (most confident of
  today's games, else the next), F1 top-3 win bars, NBA points leader,
  NFL anytime-TD top 3, PL projected-table top + biggest delta. No
  record or performance metric appears in this view — including the
  KPI strip, which is computed from the same states.
- **Filtered view = deck.** Deck header with the sport's record framed
  per the design system (headline = all counted picks, pre-event
  subset beside it; **NBA excepted** — its headline is the pre-tip
  subset, because its all-counted game_outcome line is rebuilt by the
  thousand), then the sport's All cards plus deck-only cards: more
  matches (excluding ones already on the All board), player leaders,
  other markets (PL scorers with their `prior_season`/`position_avg`
  confidence label shown; F1 podium + full field), and the performance
  cards — NFL/CFB calibration bucket charts, CFB weekly-trend and PL
  gameweek-trend sparklines (fixed 0–1 domain), NBA confidence buckets
  + the `/nba/calibration` curve drawn un-smoothed, F1 points-finish
  buckets. F1's deck head leads with the win-market Brier.
- **Honesty.** Rebuilt/backfilled items (NBA `rebuilt` games and player
  rows, PL `made_before_kickoff: false`/`backfilled`, F1 sources other
  than `tracked`/`live`) are excluded from curation; a late PL/NBA match
  shown in a deck is labelled "Made after kickoff/tip-off". Prob bars
  keep header order with only the pick's segment in the sport accent.
  Times are formatted in America/Chicago with the zone shown.
- **Isolation.** Two-phase load: picks render first; records,
  calibration and freshness lines arrive in phase 2 and re-render, so
  the NBA track record (which ships every per-pick row — several MB)
  never blocks the board. One dead API marks only its sport failed; its
  deck shows the EmptyState ("Couldn't load picks." / "No picks yet —
  check back soon.") and the All board renders without it.

## Tests

- Baseline (main @ 0f64617, exact CI file list, verified in a pristine
  worktree): **165 pass / 0 fail / 1 skipped** (166 tests).
- After (same list + `tests/home.test.mjs`): **175 pass / 0 fail /
  1 skipped** (176 tests). No regressions; the delta is the 11 new home
  tests minus one net test from the hub.test.mjs replacements.
  (An earlier draft of these notes quoted 198/5 figures from an
  aggregate run that swept in non-CI test files; the CI-list numbers
  above are the verified ones, and `tests/resync-workflow.test.mjs`
  passes standalone on pristine main.)
- `tests/hub.test.mjs` updates (all pin the old homepage structure):
  nav expectation gains the leading "Home" entry (comment updated);
  "one card per sport" replaced by a landing-board test (filter chips
  in order, real favicon URLs, board containers); the `.status` test
  replaced by an honest-words/12px-floor test over the new shell; the
  two teaser-slot tests and "the hub loads the teaser module" replaced
  by board-rendering and home-module tests. Token-drift, wordmark,
  title/OG, honesty-rule and links tests are unchanged and pass.
- Smoke test (kept out of the repo, run from /tmp): home.js booted
  under Node with a minimal DOM shim and `fetch` stubbed by raw-shape
  payloads derived from the Nov 1 pulls. Verified: All board renders
  ≥9 cards with the CIN 85% notched hero, the +5.3 BOS@ORL gap card,
  Dončić leader card and Mexico City GP card, and **no** performance
  text; each filter renders only that sport's cards with the correct
  deck head (PL 49% / pre-kickoff 45% n=38; CFB 75% / 68% n=347; NBA
  58% pre-tip n=114 with the 82% rebuilt line absent; F1 Brier 0.037;
  NFL calibration card "says ~81%, lands 85%").

## Stale-while-revalidate cache (follow-up, same branch)

Kevin's requirement: the home page should load faster on repeat visits —
"especially if all the predictions are already loaded, it should only
wait to load the ones that are fresh or being updated currently."
`home.js` now keeps every payload it fetches in localStorage and
revalidates by snapshot, per sport:

- **Storage.** One entry per endpoint URL under the namespace
  `predictor-home-v1`, shaped `{ t: fetched-at epoch ms, body: parsed
  JSON }`. Writes happen only in the network getter, after a response
  has parsed — a failed or partial response is never cached. A payload
  whose serialized entry exceeds ~800 KB is skipped: in practice that
  is the NBA track record (several MB of per-pick rows), which simply
  revalidates every visit. Every storage touch is try/caught; quota or
  privacy-mode failures disable the cache silently and never break
  rendering. The helpers (`readCacheEntry`, `writeCacheEntry`,
  `metaStamp`, `metaMatches`) are pure over a localStorage-shaped store
  and unit-tested with a fake.
- **Pass 0 (the cache).** On load, each sport is first run through the
  loaders with a cache-only getter. Whatever a previous visit cached
  paints immediately — board and deck data alike — through the *same*
  normalizers, builders and honesty filters as fresh data, so a cached
  pick is held to exactly the same rules (a rebuilt pick in a cached
  payload is still excluded/labelled). The freshness line is built from
  the cached snapshot-metas, so a cached board carries its data's
  as-of date. A sport the cache cannot fully serve stays out of this
  paint and is filled in by revalidation. With no cache at all, pass 0
  paints nothing and the visit behaves exactly as the first version
  did (two-phase load, picks first).
- **Revalidation.** Wave A re-checks the picks. For the four sports
  with a snapshot-meta (NFL, CFB, NBA, F1), the cheap meta is fetched
  first: if its `generated_at` matches the cached meta's, the sport's
  heavy endpoints (games / predictions / props / players) *and* its
  record are skipped entirely — an unchanged snapshot means unchanged
  data everywhere it feeds. A live meta (null `generated_at`) carries
  no stamp and can never match, so such a sport always revalidates.
  PL has no snapshot-meta route and always revalidates (its endpoints
  are the family's lightest). The NBA calibration curve rides with the
  NBA's snapshot the same way. Note the meta request now happens
  before a sport's picks rather than after (it is the gate); it is a
  few hundred bytes.
- **Patching.** When fresh data for a sport differs from what is on
  the page (compared as serialized normalized state), only that sport
  is repainted, by reconciliation: each rendered card is keyed
  (sport · view · kind · match · title) and content-hashed, and a card
  whose key and hash survive keeps its exact DOM node. The KPI strip
  and deck head repaint only when their content changes. The filter
  selection and scroll position are never touched by a background
  update. A sport the network cannot reach keeps its cached board and
  as-of line instead of being marked failed; with no cache, its empty
  state behaves as before.
- **TTL.** There is deliberately no time-based TTL: freshness is the
  snapshot stamp, not the clock. A sport whose snapshot has not moved
  is current by definition; `t` is kept in each entry for as-of
  labelling and future policy, not consulted for expiry.

Tests: five new unit tests in `tests/home.test.mjs` (round-trip,
malformed entry, oversized skip, storage-failure tolerance,
meta-match/mismatch/live rules). CI list: **180 pass / 0 fail /
1 skipped** (181 tests), up from the 175/0/1 baseline — the delta is
exactly the new tests. A new two-visit smoke harness (kept in /tmp,
like the first) verifies the behaviour end-to-end: visit 1 renders
cold and writes 28 cache entries; visit 2, with every heavy endpoint
held behind a gate, paints the full board from cache before any of
them resolves (freshness line showing the cached snapshot date), then
— gates open — never requests a single NFL or F1 endpoint (snapshot
unchanged, records included), refetches the NBA (snapshot moved;
its GSW card repaints 68% → 75%) and PL (no meta; marquee updates),
and keeps the unchanged NFL hero's DOM node identical through the
patch. The original cold-visit smoke still passes unchanged.

## Left out / stubbed

- NBA rankings card uses `/nba/hub/rankings` (verified live).
- PL freshness line: PL has no snapshot-meta route, so the footer
  freshness list omits PL rather than guessing.
- CFB midweek: with no upcoming slate the CFB deck shows its player
  leaders/TD board for the current (graded) week plus a "next slate"
  note card; its All-view presence is hero contention only.
- Match detail deep links: cards link to the sport front pages (the
  mockup's behaviour); teasers.js's per-game `?game=` links were not
  carried over.
