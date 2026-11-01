import { test } from "node:test";
import assert from "node:assert/strict";
import {
  bandFor,
  lineText,
  signed1,
  normalizePL,
  normalizeFootball,
  normalizeNBA,
  normalizeF1,
  normalizeRecord,
  bestGap,
  buildAllCards,
  buildDeckCards,
  deckHead,
  sparkPoints,
  calibPoints,
  CACHE_MAX_CHARS,
  cacheKey,
  readCacheEntry,
  writeCacheEntry,
  metaStamp,
  metaMatches,
} from "../home.js";

// Fixtures are written in the RAW shapes the five APIs serve (probed from
// the live endpoints 2026-11-01), not in home.js's normalized shape — a
// fixture that already matches the code's expectations would test nothing.

const nflWeek = { season: 2026, week: 8 };
const nflGames = [
  { game_id: "g1", gameday: "2026-11-01T17:00:00", home_team: "CIN", away_team: "TEN", home_score: null, away_score: null, spread_line: 12.5, total_line: 46.5, stadium: "Paycor Stadium" },
  { game_id: "g2", gameday: "2026-11-01T17:00:00", home_team: "BUF", away_team: "BAL", home_score: null, away_score: null, spread_line: 3.0, total_line: 54.5, stadium: "Highmark Stadium" },
  { game_id: "g3", gameday: "2026-10-29T23:15:00", home_team: "GB", away_team: "CAR", home_score: 27, away_score: 19, spread_line: 3.5, total_line: 47.5, stadium: "Lambeau Field" },
];
const nflPreds = {
  g1: { home_win_prob: 0.848, away_win_prob: 0.152, predicted_margin: 13.38, predicted_total: 42.8 },
  g2: { home_win_prob: 0.706, away_win_prob: 0.294, predicted_margin: 7.06, predicted_total: 49.71 },
  g3: { home_win_prob: 0.65, away_win_prob: 0.35, predicted_margin: 5.07, predicted_total: 47.79 },
};
const nflProps = [
  { player_name: "J.Gibbs", recent_team: "DET", position: "RB", anytime_td_prob: 0.554, rushing_yards: 74.4 },
  { player_name: "J.Burrow", recent_team: "CIN", position: "QB", anytime_td_prob: 0.21, passing_yards: 256.4 },
];

test("bandFor computes the BandChip from the probability, per market size", () => {
  assert.equal(bandFor(0.85, 2), "Strong");
  assert.equal(bandFor(0.65, 2), "Moderate");
  assert.equal(bandFor(0.51, 2), "Leaning");
  assert.equal(bandFor(0.56, 3), "Strong");
  assert.equal(bandFor(0.45, 3), "Moderate");
  assert.equal(bandFor(null), null);
});

test("lineText names the team and uses a true minus", () => {
  assert.equal(lineText("CIN", 12.5), "CIN −12.5");
  assert.equal(lineText("TEN", -12.5), "TEN +12.5");
  assert.equal(lineText("BUF", 3), "BUF −3");
  assert.equal(signed1(5.26), "+5.3");
  assert.equal(signed1(-3.82), "−3.8");
});

test("normalizeFootball drops finished games and converts the lines once", () => {
  const st = normalizeFootball("nfl", { week: nflWeek, games: nflGames, preds: nflPreds, props: nflProps });
  assert.deepEqual(st.matches.map((m) => m.id), ["g1", "g2"]);
  assert.equal(st.matches[0].pickProb, 0.848);
  assert.equal(st.matches[0].lineHome, 12.5);
  assert.equal(st.players[0].name, "J.Gibbs");
});

test("the All board leads with the most confident pick as a notched hero, and carries no performance", () => {
  const nfl = normalizeFootball("nfl", { week: nflWeek, games: nflGames, preds: nflPreds, props: nflProps });
  const cards = buildAllCards({ nfl }, new Date("2026-11-01T12:00:00Z"));
  const hero = cards[0];
  assert.equal(hero.kind, "match");
  assert.equal(hero.wide, true);
  assert.equal(hero.notch, true);
  assert.equal(hero.big, "85%");
  assert.equal(hero.band, "Strong");
  assert.match(hero.title, /Cincinnati/);
  // No records, buckets or calibration anywhere on the All board.
  for (const c of cards) assert.ok(!["buckets", "calib", "spark"].includes(c.kind), `performance card on All board: ${c.kind}`);
});

test("the gap card exists only behind a real line, and takes the biggest gap", () => {
  const nfl = normalizeFootball("nfl", { week: nflWeek, games: nflGames, preds: nflPreds, props: [] });
  const nba = normalizeNBA({
    games: [
      {
        game_id: "n1", home_team: "ORL", away_team: "BOS", tip_off: "2026-11-01T20:30Z", completed: false, rebuilt: false,
        prediction: { home_win_probability: 0.4216, predicted_margin: -2.68, predicted_total: 228.76, market_point: 6.5, market_total_point: 223.5 },
      },
    ],
    playersByGame: {},
    rankings: [],
  });
  const gap = bestGap({ nfl, nba });
  assert.equal(gap.sport, "nba");
  assert.equal(gap.kind, "total");
  assert.ok(Math.abs(gap.gap - 5.26) < 0.01);
  // Strip the market lines and the card must cease to exist.
  const noLines = normalizeNBA({
    games: [
      {
        game_id: "n2", home_team: "GSW", away_team: "LAC", tip_off: "2026-11-02T00:30Z", completed: false, rebuilt: false,
        prediction: { home_win_probability: 0.6839, predicted_margin: 6.5, predicted_total: 219.78, market_point: null, market_total_point: null },
      },
    ],
    playersByGame: {},
    rankings: [],
  });
  assert.equal(bestGap({ nba: noLines }), null);
});

test("a rebuilt NBA game is labelled late and never curated", () => {
  const nba = normalizeNBA({
    games: [
      {
        game_id: "n3", home_team: "NYK", away_team: "BKN", tip_off: "2026-11-02T23:30Z", completed: false, rebuilt: true,
        prediction: { home_win_probability: 0.95, predicted_margin: 20, predicted_total: 224, market_point: null, market_total_point: null },
      },
    ],
    playersByGame: {},
    rankings: [],
  });
  assert.equal(nba.matches[0].late, true);
  const cards = buildAllCards({ nba }, new Date("2026-11-01T12:00:00Z"));
  assert.equal(cards.filter((c) => c.kind === "match").length, 0);
});

test("NBA players pivot from one row per stat, rebuilt rows dropped", () => {
  const nba = normalizeNBA({
    games: [],
    playersByGame: {
      n1: [
        { player_id: "1", player_name: "Luka Doncic", team: "LAL", stat: "points", predicted_value: 35.2, rebuilt: false },
        { player_id: "1", player_name: "Luka Doncic", team: "LAL", stat: "rebounds", predicted_value: 7.2, rebuilt: false },
        { player_id: "1", player_name: "Luka Doncic", team: "LAL", stat: "assists", predicted_value: 7.9, rebuilt: false },
        { player_id: "2", player_name: "Ghost", team: "LAL", stat: "points", predicted_value: 99, rebuilt: true },
      ],
    },
    rankings: [],
  });
  assert.equal(nba.players.n1.length, 1);
  assert.equal(nba.players.n1[0].points, 35.2);
  assert.equal(nba.players.n1[0].assists, 7.9);
});

test("records frame the headline as all counted, pre-event beside it — except the NBA", () => {
  const nflRec = normalizeRecord("nfl", {
    games: { n_resolved: 108, pct_moneyline_correct: 0.583, pct_ats_correct: 0.509, pct_totals_correct: 0.454, n_rebuilt: 33, pre_kickoff: { n_resolved: 75, pct_moneyline_correct: 0.587, pct_ats_correct: 0.507, pct_totals_correct: 0.493 } },
    calibration: { winner: [] },
  });
  const head = deckHead("nfl", {}, nflRec);
  assert.equal(head.big, "58%");
  assert.match(head.small, /all counted · n=108/);
  assert.match(head.small, /pre-kickoff 59% \(n=75\)/);

  const nbaRec = normalizeRecord("nba", [
    { market: "game_outcome", total_predictions: 1541, hit_rate: 0.818, pre_tip: null },
    { market: "h2h", total_predictions: 115, hit_rate: 0.574, pre_tip: { n: 114, correct: 66, hit_rate: 0.579 }, confidence_buckets: [], weekly: [] },
  ]);
  const nbaHead = deckHead("nba", {}, nbaRec);
  // The rebuilt-inflated 81.8% game_outcome line must never be the headline.
  assert.equal(nbaHead.big, "58%");
  assert.match(nbaHead.small, /pre-tip · n=114/);
});

test("PL record reads by_market, and its deck shows the gameweek trend (calibration is empty by design)", () => {
  const rec = normalizeRecord("pl", {
    summary: {
      n_resolved_fixtures: 88,
      by_market: { match_result: { pct_correct: 0.489, n_resolved: 88 } },
      pre_kickoff: { n_resolved_fixtures: 38, match_result: 0.447, over_under_2_5: 0.526, btts: 0.447, exact_score: 0.132 },
    },
    gameweeks: [
      { gameweek: 1, pct_correct: 0.6, n_fixtures: 10, n_rebuilt: 30 },
      { gameweek: 2, pct_correct: 0.7, n_fixtures: 10, n_rebuilt: 20 },
    ],
  });
  assert.equal(rec.headline.pct, 0.489);
  assert.equal(rec.beside.pct, 0.447);
  assert.match(rec.note, /50 of 88 fixtures carry a “Made after kickoff” label/);
  const st = normalizePL({ gw: { gameweek: 9, fixtures: [] }, playersByFixture: {}, table: null });
  const deck = buildDeckCards("pl", st, rec, [], new Set());
  const trend = deck.find((c) => c.kind === "spark");
  assert.ok(trend, "PL deck carries the gameweek trend card");
  assert.equal(trend.stats[1].v, "53%");
});

test("F1 shows only pre-event sources, and its deck head leads with Brier", () => {
  const races = [{ season: 2026, round: 19, race_name: "Mexico City Grand Prix", race_datetime: "2026-11-01T20:00:00+00:00", completed: false }];
  const live = normalizeF1({ races, pred: { source: "live", tier: "post_qualifying", predictions: [{ driver_id: "russell", driver_name: "Russell", constructor_id: "mercedes", p_win: 0.1619, p_podium: 0.4447 }] } });
  assert.equal(live.drivers.length, 1);
  const rebuilt = normalizeF1({ races, pred: { source: "rebuilt", tier: "post_qualifying", predictions: [{ driver_id: "russell", p_win: 0.9 }] } });
  assert.equal(rebuilt.drivers.length, 0);
  const rec = normalizeRecord("f1", { by_market: [{ market: "win", tier: "post_qualifying", n: 352, brier: 0.0373 }], calibration: { points_finish: [] }, n_resolved: 2816, n_pre_session: 1760 });
  assert.equal(deckHead("f1", {}, rec).big, "0.037");
});

// --- the stale-while-revalidate cache --------------------------------------
//
// The cache helpers are driven with a fake localStorage-shaped store:
// the same { getItem, setItem, removeItem } surface the browser gives
// home.js, with failure modes a Map cannot express added per test.

const fakeStore = () => {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
};

test("cache entries round-trip under the versioned namespace", () => {
  const store = fakeStore();
  const body = { fixtures: [{ event_id: "84" }] };
  assert.equal(writeCacheEntry(store, "/pl/api/fixtures/gameweek", body, 1761970000000), true);
  assert.equal(cacheKey("/pl/api/fixtures/gameweek"), "predictor-home-v1:/pl/api/fixtures/gameweek");
  const entry = readCacheEntry(store, "/pl/api/fixtures/gameweek");
  assert.equal(entry.t, 1761970000000);
  assert.deepEqual(entry.body, body);
  // A different URL is a different entry; a missing one reads as null.
  assert.equal(readCacheEntry(store, "/nfl/api/track-record"), null);
});

test("a malformed cache entry reads as a miss, never as data", () => {
  const store = fakeStore();
  store.setItem(cacheKey("/x"), "not json");
  assert.equal(readCacheEntry(store, "/x"), null);
  store.setItem(cacheKey("/x"), JSON.stringify({ body: {} })); // no fetched-at
  assert.equal(readCacheEntry(store, "/x"), null);
  store.setItem(cacheKey("/x"), JSON.stringify({ t: 1 })); // no body
  assert.equal(readCacheEntry(store, "/x"), null);
});

test("an oversized payload is skipped, not stored", () => {
  const store = fakeStore();
  const big = { rows: "x".repeat(CACHE_MAX_CHARS) };
  assert.equal(writeCacheEntry(store, "/nba/hub/track-record", big), false);
  assert.equal(readCacheEntry(store, "/nba/hub/track-record"), null);
  assert.equal(store.map.size, 0);
});

test("storage failures disable the cache silently", () => {
  const quota = {
    getItem: () => null,
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => {},
  };
  assert.equal(writeCacheEntry(quota, "/x", { a: 1 }), false);
  const broken = {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => {},
    removeItem: () => {},
  };
  assert.equal(readCacheEntry(broken, "/x"), null);
  // A null store (localStorage untouchable) is a disabled cache.
  assert.equal(writeCacheEntry(null, "/x", { a: 1 }), false);
  assert.equal(readCacheEntry(null, "/x"), null);
});

test("metaMatches: only an identical snapshot stamp skips a refetch", () => {
  const snap = { generated_at: "2026-11-01T00:15:26Z", source: "public_snapshot" };
  assert.equal(metaStamp(snap), "2026-11-01T00:15:26Z");
  assert.equal(metaMatches(snap, { generated_at: "2026-11-01T00:15:26Z", source: "public_snapshot" }), true);
  assert.equal(metaMatches(snap, { generated_at: "2026-11-01T06:00:00Z", source: "public_snapshot" }), false);
  // A live meta carries no stamp, and no stamp can never match: the
  // sport revalidates rather than trusting an unprovable "unchanged".
  assert.equal(metaStamp({ generated_at: null, source: "live" }), null);
  assert.equal(metaMatches({ generated_at: null, source: "live" }, { generated_at: null, source: "live" }), false);
  assert.equal(metaMatches(null, snap), false);
  assert.equal(metaMatches(snap, undefined), false);
  assert.equal(metaStamp({ generated_at: "not a date" }), null);
});

test("spark and calibration points stay inside their plots", () => {
  const pts = sparkPoints([0.6, 0.3, 0.5]).split(" ").map((p) => p.split(",").map(Number));
  assert.equal(pts.length, 3);
  for (const [x, y] of pts) {
    assert.ok(x >= 0 && x <= 100 && y >= 0 && y <= 36);
  }
  assert.equal(calibPoints([{ predicted: 0.5, actual: 0.5, n: 3 }]), ""); // one point is not a line
  assert.equal(calibPoints([{ predicted: 0.2, actual: 0.6, n: 10 }, { predicted: 0.8, actual: 0.2, n: 10 }]), "20,25.6 80,51.2");
});
