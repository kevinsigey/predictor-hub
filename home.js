// home.js — the Predictor hub landing board.
//
// The successor to the five static sport cards: a Kalshi-style grid of
// market cards curated at runtime from the five sport APIs, through the
// same same-origin proxy paths teasers.js already uses (/pl/api/…,
// /f1/api/…, /nfl/api/…, /cfb/api/…, and the NBA's unprefixed /nba/…).
//
// The structure follows teasers.js on purpose. Pure selectors and builders
// over plain data first (exported, unit-tested, no DOM, no fetch), then a
// thin painting layer that builds elements with textContent only — every
// string on the page comes from an API payload. Each sport is fetched as
// its own promise chain, so one dead API costs that sport its cards and
// nothing else: the page never blanks.
//
// The honesty rules from PRODUCT.md bind every card:
// - Only picks made before the event count toward records and curation.
//   A game or fixture the API flags as rebuilt/backfilled is excluded from
//   the All board and, where it is shown in a deck, labelled
//   "Made after kickoff" (or "tip-off" / "the session") — never hidden,
//   never in a headline subset.
// - No edge is shown without a real line behind it: the model-vs-market
//   card only exists when a payload carries an actual market line.
// - Records are framed the design system's way: the headline is ALL
//   counted picks, with the pre-event subset beside it. The NBA is the
//   family's one exception — its headline is the pre-tip-off subset.
// - Design-system vocabulary only: pick, Next up, Called it ✓, Missed ✗,
//   No pick yet, Toss-up, Most likely score. Numbers are formatted —
//   whole percents, a true minus sign, times with their zone.
//
// Repeat visits are stale-while-revalidate: every payload a visit fetches
// is cached in localStorage, so the next visit paints the board from the
// cache at once — through the same normalizers and builders as fresh
// data, so the honesty rules cannot drift between the two paths — and
// then revalidates in the background. A sport whose snapshot-meta still
// carries the cached generated_at is not refetched at all; a sport whose
// data changed is repainted in place, card by card, leaving the reader's
// filter and scroll untouched. See the cache section below.

import { pct, driverName, freshness } from "./teasers.js";

export { pct };

// --- formatting ------------------------------------------------------------

/** One decimal place, for projections and totals (35.2, 228.8). A
 *  non-number is a dash, so a null can never reach the page as "NaN". */
export const num1 = (x) =>
  typeof x === "number" && Number.isFinite(x) ? String(Math.round(x * 10) / 10) : "—";

/** A signed gap with a true minus: +5.3 / −3.8. */
export const signed1 = (x) => {
  if (typeof x !== "number" || !Number.isFinite(x)) return "—";
  const v = Math.round(Math.abs(x) * 10) / 10;
  return `${x >= 0 ? "+" : "−"}${v}`;
};

/** A spread written the design system's way — the team named, a true
 *  minus: "CIN −12.5" when Cincinnati is favoured by 12.5, "TEN +12.5"
 *  when it is getting the points. `favouredBy` is points from the named
 *  team's side: positive means favoured. */
export function lineText(team, favouredBy) {
  if (typeof favouredBy !== "number" || !Number.isFinite(favouredBy)) return "";
  const v = Math.round(Math.abs(favouredBy) * 10) / 10;
  if (v === 0) return `${team} PK`;
  return favouredBy > 0 ? `${team} −${v}` : `${team} +${v}`;
}

/** The BandChip: a pick's confidence in words, computed from its
 *  probability for the market's number of outcomes — never hand-set.
 *  Two-way: 75%+ Strong, 60%+ Moderate. Three-way (a PL result): the same
 *  words start lower, because 55% in a three-way market is a stronger
 *  statement than 55% in a two-way one. Anything else is Leaning. */
export function bandFor(prob, outcomes = 2) {
  if (typeof prob !== "number" || !Number.isFinite(prob)) return null;
  const strong = outcomes === 3 ? 0.55 : 0.75;
  const moderate = outcomes === 3 ? 0.42 : 0.6;
  if (prob >= strong) return "Strong";
  if (prob >= moderate) return "Moderate";
  return "Leaning";
}

const CT = "America/Chicago";
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: CT, year: "numeric", month: "2-digit", day: "2-digit" });
const timeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: CT,
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});
const weekdayFmt = new Intl.DateTimeFormat("en-US", { timeZone: CT, weekday: "short", month: "short", day: "numeric" });

/** The CT calendar day of an instant, as a comparable string. */
const ctDay = (d) => dayFmt.format(d);

/** "Today 12:00 PM CDT" / "Mon, Nov 2 · 2:00 PM CST". The zone is always
 *  shown, per the design system. An unparseable time is an empty string —
 *  the card renders without a time rather than inventing one. */
export function fmtWhen(iso, now = new Date()) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = timeFmt.format(d);
  if (ctDay(d) === ctDay(now)) return `Today ${time}`;
  const tomorrow = new Date(now.getTime() + 24 * 3600 * 1000);
  if (ctDay(d) === ctDay(tomorrow)) return `Tomorrow ${time}`;
  return `${weekdayFmt.format(d)} · ${time}`;
}

/** Just the clock time with its zone: "2:00 PM CDT". */
export const fmtTime = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : timeFmt.format(d);
};

/** Polyline points for a sparkline over a fixed 0..1 domain, so a 30%
 *  week looks like a dip and a 85% week like a peak — the axis never
 *  rescales quietly to flatter the line. */
export function sparkPoints(values, w = 100, h = 36) {
  const nums = (values || []).filter((v) => typeof v === "number" && Number.isFinite(v));
  if (nums.length < 2) return "";
  return nums
    .map((v, i) => `${Math.round(((i * w) / (nums.length - 1)) * 10) / 10},${Math.round((h - 2 - v * (h - 4)) * 10) / 10}`)
    .join(" ");
}

/** Calibration polyline points (predicted on x, actual on y) for a
 *  100x64 plot against the dashed perfect line. */
export function calibPoints(bins) {
  const pts = (bins || [])
    .filter((b) => Number.isFinite(b?.predicted) && Number.isFinite(b?.actual))
    .map((b) => `${Math.round(b.predicted * 1000) / 10},${Math.round((64 - b.actual * 64) * 10) / 10}`);
  return pts.length >= 2 ? pts.join(" ") : "";
}

// --- the family ------------------------------------------------------------

const HOST = "40-160-91-131.sslip.io";
export const SPORTS_META = {
  pl: { name: "Premier League", short: "PL", site: `https://pl.${HOST}`, favicon: `https://pl.${HOST}/favicon.svg`, open: "Open PL →", moment: "kickoff" },
  nfl: { name: "NFL", short: "NFL", site: `https://sports.${HOST}/?sport=nfl`, favicon: `https://sports.${HOST}/favicons/nfl.svg`, open: "Open NFL →", moment: "kickoff" },
  cfb: { name: "College football", short: "CFB", site: `https://sports.${HOST}/?sport=cfb`, favicon: `https://sports.${HOST}/favicons/cfb.svg`, open: "Open CFB →", moment: "kickoff" },
  nba: { name: "NBA", short: "NBA", site: `https://nba.${HOST}`, favicon: `https://nba.${HOST}/favicon.svg`, open: "Open NBA →", moment: "tip-off" },
  f1: { name: "Formula 1", short: "F1", site: `https://f1.${HOST}`, favicon: `https://f1.${HOST}/favicon.svg`, open: "Open F1 →", moment: "the session" },
};
const SPORT_ORDER = ["pl", "nfl", "cfb", "nba", "f1"];

// Full names for the question-format titles ("Will Cincinnati beat
// Tennessee?"). The football and NBA APIs serve abbreviations; PL team
// names and CFB team names arrive in full from their payloads.
const NFL_NAMES = {
  ARI: "Arizona", ATL: "Atlanta", BAL: "Baltimore", BUF: "Buffalo", CAR: "Carolina", CHI: "Chicago",
  CIN: "Cincinnati", CLE: "Cleveland", DAL: "Dallas", DEN: "Denver", DET: "Detroit", GB: "Green Bay",
  HOU: "Houston", IND: "Indianapolis", JAX: "Jacksonville", KC: "Kansas City", LAC: "LA Chargers",
  LA: "LA Rams", LAR: "LA Rams", LV: "Las Vegas", MIA: "Miami", MIN: "Minnesota", NE: "New England",
  NO: "New Orleans", NYG: "NY Giants", NYJ: "NY Jets", PHI: "Philadelphia", PIT: "Pittsburgh",
  SEA: "Seattle", SF: "San Francisco", TB: "Tampa Bay", TEN: "Tennessee", WAS: "Washington",
};
const NBA_NAMES = {
  ATL: "Atlanta", BOS: "Boston", BKN: "Brooklyn", CHA: "Charlotte", CHI: "Chicago", CLE: "Cleveland",
  DAL: "Dallas", DEN: "Denver", DET: "Detroit", GSW: "Golden State", HOU: "Houston", IND: "Indiana",
  LAC: "LA Clippers", LAL: "LA Lakers", MEM: "Memphis", MIA: "Miami", MIL: "Milwaukee", MIN: "Minnesota",
  NOP: "New Orleans", NYK: "New York", OKC: "Oklahoma City", ORL: "Orlando", PHI: "Philadelphia",
  PHX: "Phoenix", POR: "Portland", SAC: "Sacramento", SAS: "San Antonio", TOR: "Toronto", UTA: "Utah", WAS: "Washington",
};
const fullName = (map, code) => map[code] || code;

/** Constructor names for F1 rows, title-cased like the F1 site shows them. */
const constructorName = (id) =>
  ({ mercedes: "Mercedes", ferrari: "Ferrari", red_bull: "Red Bull", mclaren: "McLaren", rb: "RB", williams: "Williams", aston_martin: "Aston Martin", alpine: "Alpine", haas: "Haas", sauber: "Sauber" })[id] ||
  driverName(id);

// --- normalizers -----------------------------------------------------------
//
// One normalized match shape for every head-to-head sport:
// { id, home, away, homeName, awayName, start, pickSide, pickProb,
//   probs: { home, draw, away }, margin, total, lineHome, marketTotal,
//   late, final }
// `lineHome` is points the HOME team is favoured by (positive = favoured)
// whatever the API's own convention — each normalizer converts once, here,
// so card builders never reason about signs twice. `late` marks a pick the
// API says was made after the start (rebuilt / backfilled / made_before_*
// false): excluded from curation, labelled where shown.

const confOf = (m) => Math.max(m.probs.home, m.probs.away ?? 0, m.probs.draw ?? 0);

export function normalizePL({ gw, playersByFixture, table } = {}) {
  const fixtures = (gw?.fixtures || []).filter((f) => !f.finished);
  const matches = fixtures.map((f) => {
    const probs = { home: f.predicted_home_win, draw: f.predicted_draw, away: f.predicted_away_win };
    const pickSide = probs.draw >= Math.max(probs.home, probs.away) ? "draw" : probs.home >= probs.away ? "home" : "away";
    return {
      id: f.event_id,
      home: f.team_home,
      away: f.team_away,
      homeName: f.team_home,
      awayName: f.team_away,
      start: f.commence_time,
      pickSide,
      pickProb: probs[pickSide],
      probs,
      scoreline: f.predicted_scoreline || null,
      // An edge is only ever carried when the feed says a real price
      // exists — checked per fixture, as teasers.js does.
      edge: f.has_live_odds && (f.value_bet_flags || []).length ? f.value_bet_flags : null,
      late: f.made_before_kickoff === false || f.backfilled === true,
      final: null,
    };
  });
  const players = plPlayers(matches, playersByFixture);
  const tableRows = Array.isArray(table) ? table : table?.table || [];
  return { sport: "pl", gameweek: gw?.gameweek ?? gw?.current_gameweek ?? null, matches, players, table: tableRows };
}

/** PL scorer rows from per-fixture player payloads, keyed by fixture id.
 *  Split out of normalizePL so the deferred players load (Wave B) builds
 *  exactly the map the full load builds. */
export function plPlayers(matches, playersByFixture) {
  const players = {};
  for (const [id, payload] of Object.entries(playersByFixture || {})) {
    if (!payload) continue;
    const fx = (matches || []).find((m) => String(m.id) === String(id));
    const rows = [
      ...(payload.home_players || []).map((p) => ({ ...p, team: fx?.homeName || "" })),
      ...(payload.away_players || []).map((p) => ({ ...p, team: fx?.awayName || "" })),
    ]
      .filter((p) => Number.isFinite(p.anytime_goal_prob))
      .sort((a, b) => b.anytime_goal_prob - a.anytime_goal_prob);
    if (rows.length) players[id] = rows;
  }
  return players;
}

export function normalizeFootball(sport, { week, games, preds, props } = {}) {
  const names = sport === "nfl" ? NFL_NAMES : {};
  const matches = (games || [])
    .filter((g) => g.home_score == null && g.away_score == null)
    .map((g) => {
      const p = (preds || {})[g.game_id];
      if (!p || !Number.isFinite(p.home_win_prob)) return null;
      const homeProb = p.home_win_prob;
      const awayProb = p.away_win_prob ?? 1 - homeProb;
      return {
        id: g.game_id,
        home: g.home_team,
        away: g.away_team,
        homeName: fullName(names, g.home_team),
        awayName: fullName(names, g.away_team),
        start: g.gameday,
        pickSide: homeProb >= awayProb ? "home" : "away",
        pickProb: Math.max(homeProb, awayProb),
        probs: { home: homeProb, away: awayProb },
        margin: p.predicted_margin ?? null,
        total: p.predicted_total ?? null,
        // The games payload's spread_line is points the home team is
        // favoured by; the total_line is the market total. Both are real
        // lines served with the game, so both may sit behind an edge.
        lineHome: g.spread_line ?? null,
        marketTotal: g.total_line ?? null,
        stadium: g.stadium || null,
        late: false,
        final: null,
      };
    })
    .filter(Boolean)
    .sort((a, b) => new Date(a.start) - new Date(b.start));
  const players = (props || []).map((p) => ({
    name: p.player_name,
    team: p.recent_team,
    pos: p.position,
    passing: p.passing_yards,
    rushing: p.rushing_yards,
    receiving: p.receiving_yards,
    tdProb: p.anytime_td_prob,
  }));
  return { sport, week: week?.week ?? null, season: week?.season ?? null, matches, players };
}

export function normalizeNBA({ games, playersByGame, rankings } = {}) {
  const matches = (games || [])
    .filter((g) => !g.completed && g.prediction)
    .map((g) => {
      const p = g.prediction;
      const homeProb = p.home_win_prob ?? p.home_win_probability;
      if (!Number.isFinite(homeProb)) return null;
      return {
        id: g.game_id,
        home: g.home_team,
        away: g.away_team,
        homeName: fullName(NBA_NAMES, g.home_team),
        awayName: fullName(NBA_NAMES, g.away_team),
        start: g.tip_off,
        pickSide: homeProb >= 0.5 ? "home" : "away",
        pickProb: Math.max(homeProb, 1 - homeProb),
        probs: { home: homeProb, away: 1 - homeProb },
        margin: p.predicted_margin ?? null,
        total: p.predicted_total ?? null,
        // market_point is the book's home spread in the book's own sign
        // (+6.5 means the home team gets 6.5 points), the opposite of the
        // football convention — converted here: favoured-by is its negation.
        lineHome: typeof p.market_point === "number" ? -p.market_point : null,
        marketTotal: p.market_total_point ?? null,
        late: g.rebuilt === true,
        final: null,
      };
    })
    .filter(Boolean)
    .sort((a, b) => new Date(a.start) - new Date(b.start));
  // Player rows arrive one per player per stat; pivot on player_id.
  const players = nbaPlayers(playersByGame);
  return { sport: "nba", matches, players, rankings: rankings || [] };
}

/** NBA player rows pivoted per game. Split out of normalizeNBA so the
 *  deferred players load (Wave B) builds exactly the map the full
 *  load builds. */
export function nbaPlayers(playersByGame) {
  const players = {};
  for (const [gameId, rows] of Object.entries(playersByGame || {})) {
    if (!Array.isArray(rows)) continue;
    const byPlayer = new Map();
    for (const r of rows) {
      if (!r || r.rebuilt) continue;
      const key = r.player_id ?? r.player_name;
      if (!byPlayer.has(key)) byPlayer.set(key, { name: r.player_name, team: r.team });
      if (["points", "rebounds", "assists", "threes"].includes(r.stat)) byPlayer.get(key)[r.stat] = r.predicted_value;
    }
    const list = [...byPlayer.values()].filter((p) => Number.isFinite(p.points)).sort((a, b) => b.points - a.points);
    if (list.length) players[gameId] = list;
  }
  return players;
}

const isPreEvent = (source) => source === "tracked" || source === "live";

export function normalizeF1({ races, pred, now = Date.now() } = {}) {
  const race = (races || []).filter((r) => !r.completed)[0] || null;
  if (!race) return { sport: "f1", race: null, drivers: [], source: null, tier: null };
  // Same whitelist as teasers.js: a rebuilt or backtest prediction was
  // made after the session and is never shown as a call.
  if (!pred || !isPreEvent(pred.source)) {
    return { sport: "f1", race, drivers: [], source: pred?.source ?? null, tier: pred?.tier ?? null };
  }
  const drivers = (pred.predictions || [])
    .filter((d) => Number.isFinite(d.p_win))
    .slice()
    .sort((a, b) => b.p_win - a.p_win)
    .map((d) => ({
      name: driverName(d.driver_id, d.driver_name),
      constructor: constructorName(d.constructor_id),
      win: d.p_win,
      podium: d.p_podium,
      points: d.p_points_finish,
      expPos: d.expected_position,
    }));
  return { sport: "f1", race, drivers, source: pred.source, tier: pred.tier || null, late: false };
}

// --- records ---------------------------------------------------------------
//
// Normalized per sport by normalizeRecord: { headline: {pct, n, label},
// beside: {pct, n} | null, note, perf: {...} }. The framing is the design
// system's: headline = all counted picks, pre-event subset beside it —
// except the NBA, whose headline IS its pre-tip subset (its all-counted
// game_outcome line is rebuilt by the thousand and the family agreed the
// pre-tip number leads there).

export function normalizeRecord(sport, tr) {
  if (!tr) return null;
  if (sport === "pl") {
    const s = tr.summary || {};
    const all = s.by_market?.match_result;
    if (!all) return null;
    const pre = s.pre_kickoff || {};
    const preResult = pre.by_market?.match_result || pre.match_result;
    const nRebuilt = (tr.gameweeks || []).reduce((acc, g) => acc + (g.n_rebuilt || 0), 0);
    return {
      headline: { pct: all.pct_correct, n: all.n_resolved, label: "Match record · all counted" },
      beside: typeof preResult === "number" ? { pct: preResult, n: pre.n_resolved_fixtures ?? s.n_pre_kickoff } : null,
      note:
        nRebuilt > 0
          ? `${nRebuilt} of ${s.n_resolved_fixtures ?? all.n_resolved} fixtures carry a “Made after kickoff” label.`
          : "Every fixture's pick was made before kickoff.",
      perf: {
        trend: (tr.gameweeks || []).map((g) => ({ label: `GW${g.gameweek}`, value: g.pct_correct, n: g.n_fixtures })),
        markets: {
          result: pre.by_market?.match_result?.pct_correct ?? (typeof preResult === "number" ? preResult : undefined),
          ou: pre.by_market?.over_under_2_5?.pct_correct ?? pre.over_under_2_5,
          btts: pre.by_market?.btts?.pct_correct ?? pre.btts,
          exact: pre.by_market?.exact_score?.pct_correct ?? pre.exact_score,
          preN: pre.n_resolved_fixtures ?? s.n_pre_kickoff ?? null,
        },
      },
    };
  }
  if (sport === "nfl" || sport === "cfb") {
    const g = tr.games || {};
    if (!Number.isFinite(g.pct_moneyline_correct)) return null;
    const pre = g.pre_kickoff || {};
    return {
      headline: { pct: g.pct_moneyline_correct, n: g.n_resolved, label: "ML record · all counted" },
      beside: Number.isFinite(pre.pct_moneyline_correct) ? { pct: pre.pct_moneyline_correct, n: pre.n_resolved } : null,
      note:
        sport === "nfl" && g.vs_market
          ? `Mean edge vs market ${signed1(g.vs_market.mean_edge_points)} pts; on disagreements the model is right ${pct(g.vs_market.disagreement_hit_rate)} (n=${g.vs_market.disagreement_n}).`
          : "",
      perf: {
        calibration: (tr.calibration?.winner || []).filter((b) => b.n > 0 && Number.isFinite(b.hit_rate)),
        trend: (pre.weekly_trend || g.weekly_trend || g.weekly || [])
          .filter((w) => Number.isFinite(w.pct_moneyline_correct))
          .map((w) => ({ label: `W${w.week ?? ""}`.trim(), value: w.pct_moneyline_correct, n: w.n_games })),
        markets: {
          ats: { all: g.pct_ats_correct, pre: pre.pct_ats_correct },
          totals: { all: g.pct_totals_correct, pre: pre.pct_totals_correct },
        },
      },
    };
  }
  if (sport === "nba") {
    const markets = Array.isArray(tr) ? tr : [];
    const h2h = markets.find((m) => m.market === "h2h");
    if (!h2h?.pre_tip) return null;
    const byMarket = {};
    for (const key of ["h2h", "spread", "total"]) {
      const m = markets.find((x) => x.market === key);
      if (m?.pre_tip) byMarket[key] = { pct: m.pre_tip.hit_rate, n: m.pre_tip.n };
    }
    return {
      // The family's one exception: the pre-tip subset is the headline.
      headline: { pct: h2h.pre_tip.hit_rate, n: h2h.pre_tip.n, label: "H2H record · pre-tip" },
      beside: null,
      note: "The NBA headline is the pre-tip-off subset — the one exception in the family, because its all-counted line is rebuilt by the thousand.",
      perf: {
        buckets: h2h.confidence_buckets || [],
        weeks: (h2h.weekly || []).filter((w) => w.tracked && w.n > 0),
        markets: byMarket,
      },
    };
  }
  if (sport === "f1") {
    const winPostQual = (tr.by_market || []).find((m) => m.market === "win" && m.tier === "post_qualifying");
    const winAny = (tr.by_market || []).find((m) => m.market === "win");
    const w = winPostQual || winAny;
    if (!w) return null;
    return {
      headline: { pct: null, brier: w.brier, n: w.n, label: `Win-market Brier · ${String(w.tier || "").replace(/_/g, " ")}` },
      beside: null,
      note: "Judge it on Brier, not single-pick hits — a win pick is one driver in twenty-two.",
      perf: { buckets: tr.calibration?.points_finish || [], nResolved: tr.n_resolved, nPre: tr.n_pre_session },
    };
  }
  return null;
}

export function normalizeNBAcalibration(cal) {
  if (!Array.isArray(cal)) return [];
  return cal
    .map((b) => ({ predicted: b.predicted_rate ?? b.pred, actual: b.actual_rate ?? b.actual, n: b.count ?? b.n }))
    .filter((b) => Number.isFinite(b.predicted) && Number.isFinite(b.actual));
}

// --- card builders ---------------------------------------------------------
//
// A card is a plain object; the painting layer turns it into one <a>.
// { sport, view: "all" | "deck", kind, tag, time, title, band, big,
//   bigWin, lab: [..], segments: [{w, color}], legend: [..],
//   pills: [{t, cls}], meter: {fill, mark}, rows: [{name, sub, value,
//   width}], stats: [{k, v}], buckets: [{bv, h, bl}], spark, calib,
//   note, foot, wide, notch, empty }

const SEG_ACCENT = "var(--color-pr-accent)";
const SEG_MUTE = "var(--color-pr-fill-mute)";
const SEG_DARK = "var(--color-pr-rule)";
const SEG_DIM = "var(--color-pr-text-dim)";

const pickName = (m) => (m.pickSide === "home" ? m.homeName : m.pickSide === "away" ? m.awayName : "Draw");
const otherName = (m) => (m.pickSide === "home" ? m.awayName : m.homeName);

/** A two- or three-way match card. Segments stay in header order (home
 *  first, draw in the middle); the pick's segment takes the sport accent
 *  and the rest take the neutral ramp, per ProbabilityBar. */
export function matchCard(sport, m, { view = "all", tag = "Match", wide = false, hero = false } = {}) {
  const meta = SPORTS_META[sport];
  const threeWay = typeof m.probs.draw === "number";
  const title =
    sport === "pl"
      ? `${m.homeName} v ${m.awayName} — result?`
      : m.pickProb >= 0.5
        ? `Will ${pickName(m)} beat ${otherName(m)}?`
        : `${m.homeName} v ${m.awayName} — result?`;
  const segments = threeWay
    ? [
        { w: m.probs.home * 100, color: m.pickSide === "home" ? SEG_ACCENT : SEG_MUTE },
        { w: m.probs.draw * 100, color: m.pickSide === "draw" ? SEG_ACCENT : SEG_DARK },
        { w: m.probs.away * 100, color: m.pickSide === "away" ? SEG_ACCENT : SEG_DIM },
      ]
    : [
        { w: m.probs.home * 100, color: m.pickSide === "home" ? SEG_ACCENT : SEG_MUTE },
        { w: m.probs.away * 100, color: m.pickSide === "away" ? SEG_ACCENT : SEG_MUTE },
      ];
  const legend = threeWay
    ? [`${m.homeName} ${pct(m.probs.home)}`, `Draw ${pct(m.probs.draw)}`, `${m.awayName} ${pct(m.probs.away)}`]
    : [`${m.homeName} ${pct(m.probs.home)}`, `${m.awayName} ${pct(m.probs.away)}`];
  const lab = [];
  if (hero) lab.push("The most confident call on the board.");
  if (m.pickProb < 0.52 && !threeWay) lab.push("A toss-up by the model's numbers.");
  if (typeof m.lineHome === "number") {
    const favoured = m.pickSide === "home" ? m.lineHome : -m.lineHome;
    lab.push(`Model line ${lineText(pickName(m) === m.homeName ? m.home : m.away, favoured)}`);
  }
  if (typeof m.total === "number") lab.push(`Projected total ${num1(m.total)}`);
  if (m.scoreline) lab.push(`Most likely score ${m.scoreline}`);
  if (m.late) lab.push(`Made after ${meta.moment} — shown, never counted.`);
  return {
    sport,
    view,
    kind: "match",
    tag: `${meta.short} · ${tag}${hero ? " · Next up" : ""}`,
    time: "",
    startISO: m.start,
    title,
    band: bandFor(m.pickProb, threeWay ? 3 : 2),
    big: pct(m.pickProb),
    bigWin: m.pickProb >= 0.7,
    lab,
    segments,
    legend,
    foot: m.stadium || (threeWay ? `Gameweek pick · made before ${meta.moment}` : `Made before ${meta.moment}`),
    wide,
    notch: hero,
    matchId: m.id,
  };
}

/** The consensus card: the most confident call, made before kickoff,
 *  that AGREES with the market. It replaces the old biggest-gap card
 *  (removed 2026-11-17): the paper ledger's record is that the model's
 *  largest disagreements with the market are its worst calls — CFB
 *  spread dogs priced 15+ points from the model hit 25% against a 37%
 *  price — so the lead card no longer promotes the weakest signal on
 *  the board. Agreement is read off the spread line: `lineHome` is
 *  points the home team is favoured by, so the market's favourite is
 *  home when it is positive and away when negative, and the model's
 *  pick must be that side. A sport whose matches carry no line (PL)
 *  cannot be judged for agreement and never supplies this card.
 *  `exclude` is the hero's {sport, m}: the same game is never shown
 *  twice in a row. Returns null when nothing qualifies — an absent
 *  card renders nothing, because a placeholder would read as a claim. */
export function bestConsensus(states, exclude = null) {
  let best = null;
  for (const st of Object.values(states || {})) {
    if (!st || st.failed) continue;
    for (const m of st.matches || []) {
      if (m.late) continue;
      if (exclude && st.sport === exclude.sport && m === exclude.m) continue;
      if (!Number.isFinite(m.lineHome) || m.lineHome === 0) continue;
      const marketSide = m.lineHome > 0 ? "home" : "away";
      if (m.pickSide !== marketSide) continue;
      if (!best || m.pickProb > best.m.pickProb) best = { sport: st.sport, m, line: m.lineHome };
    }
  }
  return best;
}

export function consensusCard(c) {
  const meta = SPORTS_META[c.sport];
  const matchup = `${c.m.awayName} @ ${c.m.homeName}`;
  const favoured = c.line > 0 ? c.m.home : c.m.away;
  return {
    sport: c.sport,
    view: "all",
    kind: "consensus",
    tag: `${meta.short} · Consensus`,
    time: "",
    startISO: c.m.start,
    title: `${matchup} — model and market agree`,
    big: pct(c.m.pickProb),
    lab: [`${pickName(c.m)} to win`, `market line ${lineText(favoured, Math.abs(c.line))}`],
    meter: { fill: c.m.pickProb * 100, mark: 50 },
    legend: [`Model ${pct(c.m.pickProb)}`, `Market ${lineText(favoured, Math.abs(c.line))}`],
    pills: [
      { t: `Pick: ${pickName(c.m)} ${pct(c.m.pickProb)}`, cls: "yes" },
      { t: `Market agrees`, cls: "lean" },
    ],
    foot: "Most confident call the market agrees with",
  };
}

/** Bars card: F1 win contenders, anytime-TD leaders, PL scorers, podium. */
export function barsCard(sport, { view, tag, time = "", title, rows, foot }) {
  return { sport, view, kind: "bars", tag: `${SPORTS_META[sport].short} · ${tag}`, time, title, rows, foot };
}

/** The All board. Predictions only — no performance, no records: those
 *  live in the decks. Curation order is the mockup's: hero, the
 *  consensus, one marquee per sport, then the player stories. A sport
 *  with nothing upcoming contributes nothing rather than a placeholder. */
export function buildAllCards(states, now = new Date()) {
  const cards = [];
  const usable = SPORT_ORDER.map((s) => states?.[s]).filter((st) => st && !st.failed);
  // The hero: the most confident match pick across the head-to-head
  // sports. F1 win probabilities are one-in-twenty markets and are never
  // compared against two-way picks.
  let hero = null;
  for (const st of usable) {
    for (const m of st.matches || []) {
      if (m.late) continue;
      if (!hero || m.pickProb > hero.m.pickProb) hero = { sport: st.sport, m };
    }
  }
  const consensus = bestConsensus(states, hero);
  if (hero) cards.push(matchCard(hero.sport, hero.m, { view: "all", wide: true, hero: true }));
  if (consensus) cards.push(consensusCard(consensus));

  const todayStr = ctDay(now);
  const marqueeOf = (st) => {
    const open = (st.matches || []).filter((m) => !m.late);
    if (!open.length) return null;
    const today = open.filter((m) => ctDay(new Date(m.start)) === todayStr);
    const pool = today.length ? today : open;
    return pool.slice().sort((a, b) => b.pickProb - a.pickProb)[0];
  };

  for (const st of usable) {
    if (st.sport === "f1") {
      if (st.drivers?.length) {
        cards.push(
          barsCard("f1", {
            view: "all",
            tag: "Race winner",
            time: "",
            startISO: st.race?.race_datetime,
            title: `Who wins the ${String(st.race?.race_name || "").replace(/ Grand Prix$/, "")} GP?`,
            rows: st.drivers.slice(0, 3).map((d) => ({
              name: d.name,
              sub: d.constructor,
              value: pct(d.win),
              width: (d.win / st.drivers[0].win) * 100,
            })),
            foot: `${st.tier ? `${String(st.tier).replace(/_/g, " ")} model` : "Pre-session model"} · podium: ${st.drivers[0].name} ${pct(st.drivers[0].podium)}`,
          }),
        );
      }
      continue;
    }
    const mq = marqueeOf(st);
    if (mq && !(hero && hero.m === mq)) cards.push(matchCard(st.sport, mq, { view: "all" }));
    else if (mq && hero && hero.m === mq) {
      const next = (st.matches || []).filter((m) => !m.late && m !== mq).sort((a, b) => b.pickProb - a.pickProb)[0];
      if (next) cards.push(matchCard(st.sport, next, { view: "all" }));
    }
  }

  // Player stories: the NBA points leader, the NFL anytime-TD board, the
  // PL projected table. (CFB's player board lives in its deck: midweek
  // there is usually no upcoming CFB slate to project.)
  const nba = states?.nba;
  if (nba && !nba.failed) {
    let leader = null;
    let leaderGame = null;
    for (const [gid, list] of Object.entries(nba.players || {})) {
      const m = (nba.matches || []).find((x) => String(x.id) === String(gid));
      if (list[0] && (!leader || list[0].points > leader.points)) {
        leader = list[0];
        leaderGame = m;
      }
    }
    if (leader) {
      cards.push({
        sport: "nba",
        view: "all",
        kind: "player",
        tag: "NBA · Player",
        time: "",
        startISO: leaderGame?.start,
        title: `${leader.name} — projected line`,
        stats: [
          { k: "Points", v: num1(leader.points) },
          { k: "Rebounds", v: num1(leader.rebounds) },
          { k: "Assists", v: num1(leader.assists) },
        ],
        note: leaderGame
          ? `Top projection on the NBA board, ${leaderGame.awayName} @ ${leaderGame.homeName}.`
          : "Top projection on the NBA board.",
        foot: "Player projections, made before tip-off",
      });
    }
  }
  const nfl = states?.nfl;
  if (nfl && !nfl.failed && nfl.players?.length) {
    const td = nfl.players.filter((p) => Number.isFinite(p.tdProb)).sort((a, b) => b.tdProb - a.tdProb).slice(0, 3);
    if (td.length) {
      cards.push(
        barsCard("nfl", {
          view: "all",
          tag: "Player",
          time: nfl.week ? `Week ${nfl.week}` : "",
          title: "Anytime touchdown — most likely",
          rows: td.map((p) => ({ name: p.name, sub: `${p.team} · ${p.pos}`, value: pct(p.tdProb), width: (p.tdProb / td[0].tdProb) * 100 })),
          foot: `Drawn from ${nfl.players.length} player projections published for Week ${nfl.week}`,
        }),
      );
    }
  }
  const pl = states?.pl;
  if (pl && !pl.failed && pl.table?.length) {
    const top = pl.table.slice(0, 3);
    const delta = pl.table
      .filter((t) => typeof t.position_delta === "number")
      .sort((a, b) => Math.abs(b.position_delta) - Math.abs(a.position_delta))[0];
    const rows = top.map((t) => ({ name: `${t.projected_position} · ${t.team}`, sub: "", value: `${num1(t.projected_points)} pts` }));
    if (delta && !top.includes(delta)) {
      rows.push({ name: `${delta.projected_position} · ${delta.team}`, sub: "", value: `from ${delta.current_position}${delta.current_position === 1 ? "st" : delta.current_position === 2 ? "nd" : delta.current_position === 3 ? "rd" : "th"}` });
    }
    cards.push({
      sport: "pl",
      view: "all",
      kind: "table",
      tag: "PL · Season",
      time: "Projected table",
      title: "Projected final table — top of the story",
      rows,
      note: delta
        ? `${delta.team}'s projected move from ${delta.current_position} to ${delta.projected_position} is the table's biggest delta — the model's most arguable season call.`
        : "",
      foot: "Full 20-team projection on the PL hub",
    });
  }
  return cards;
}

// --- decks -----------------------------------------------------------------

/** The deck header for a filtered sport: the record, framed as the
 *  design system frames it, plus a one-line description built only from
 *  facts the payloads carry. Null when the record has not loaded (or the
 *  sport has none) — the deck still renders its cards. */
export function deckHead(sport, st, record) {
  const meta = SPORTS_META[sport];
  if (!record) return null;
  const h = record.headline;
  const big = typeof h.brier === "number" ? h.brier.toFixed(3) : pct(h.pct);
  const small = record.beside
    ? `${h.label} · n=${h.n} · pre-${SPORTS_META[sport].moment} ${pct(record.beside.pct)} (n=${record.beside.n})`
    : `${h.label} · n=${h.n}`;
  return { sport, title: `${meta.name} — the full deck`, desc: record.note || "", big, small };
}

function leadersRows(players, key, unit, n = 1) {
  return players
    .filter((p) => Number.isFinite(p[key]))
    .sort((a, b) => b[key] - a[key])
    .slice(0, n)
    .map((p) => ({ name: p.name, sub: `${p.team} · ${unit}`, value: num1(p[key]), width: undefined, raw: p[key] }));
}

function calibrationCard(sport, buckets, markets) {
  const usable = (buckets || []).filter((b) => b.n > 0 && Number.isFinite(b.hit_rate) && Number.isFinite(b.mean_prob));
  if (!usable.length) return null;
  const shown = usable.filter((b) => b.mean_prob >= 0.25);
  const maxHit = Math.max(...shown.map((b) => b.hit_rate), 0.01);
  const topBucket = shown[shown.length - 1];
  return {
    sport,
    view: "deck",
    kind: "buckets",
    tag: `${SPORTS_META[sport].short} · Performance`,
    time: "Calibration · winner",
    title: topBucket ? `When it says ~${pct(topBucket.mean_prob)}, it lands ${pct(topBucket.hit_rate)}` : "Calibration, drawn as served",
    buckets: shown.map((b) => ({
      bv: pct(b.hit_rate),
      h: Math.max(12, Math.round((b.hit_rate / maxHit) * 76)),
      bl: `says ~${pct(b.mean_prob)}\nn=${b.n}`,
    })),
    note: "Drawn as served — no smoothing, including the buckets that under-deliver.",
    foot: markets || "",
  };
}

/** One sport's deck: its All cards are already on the board; this builds
 *  the deck-only cards — more matches, player leaders, other markets, and
 *  the performance cards that never appear on the All board. */
export function buildDeckCards(sport, st, record, nbaCal, excludeIds) {
  if (!st || st.failed) return [];
  const skip = excludeIds || new Set();
  const restMatches = (st.matches || []).filter((m) => !skip.has(String(m.id)));
  const cards = [];
  if (sport === "pl") {
    const rest = restMatches.slice(0, 2);
    for (const m of rest) cards.push(matchCard("pl", m, { view: "deck" }));
    const first = (st.matches || [])[0];
    const scorers = first ? st.players?.[first.id] : null;
    if (first && scorers?.length) {
      const top = scorers.slice(0, 3);
      const confs = [...new Set(scorers.map((p) => p.confidence).filter(Boolean))].map((c) =>
        c === "prior_season" ? "prior season" : c === "position_avg" ? "position average" : c.replace(/_/g, " "),
      );
      cards.push(
        barsCard("pl", {
          view: "deck",
          tag: "Player",
          time: "",
          startISO: first.start,
          title: `Anytime scorer — ${first.homeName} v ${first.awayName}`,
          rows: top.map((p) => ({
            name: p.name,
            sub: `${p.team}${p.position ? ` · ${p.position}` : ""}`,
            value: pct(p.anytime_goal_prob),
            width: (p.anytime_goal_prob / top[0].anytime_goal_prob) * 100,
          })),
          foot: confs.length ? `Confidence: ${confs.join(" / ")}` : "Scorer probabilities as served",
        }),
      );
    }
    const trend = record?.perf?.trend || [];
    if (trend.length >= 2) {
      cards.push({
        sport,
        view: "deck",
        kind: "spark",
        tag: "PL · Performance",
        time: `Gameweeks 1–${trend.length}`,
        title: "Match-result hit rate by gameweek",
        spark: sparkPoints(trend.map((t) => t.value)),
        legend: [trend[0], trend[Math.floor(trend.length / 2)], trend[trend.length - 1]]
          .filter(Boolean)
          .map((t) => `${t.label} ${pct(t.value)}`),
        stats: [
          { k: "Result", v: pct(record.perf.markets.result) },
          { k: "Over 2.5", v: pct(record.perf.markets.ou) },
          { k: "BTTS", v: pct(record.perf.markets.btts) },
        ],
        foot: record.perf.markets.preN ? `Pre-kickoff subset · n=${record.perf.markets.preN} · exact score ${pct(record.perf.markets.exact)}` : "",
      });
    }
    return cards;
  }
  if (sport === "nfl" || sport === "cfb") {
    const extra = restMatches.slice(0, 4);
    for (const m of extra) cards.push(matchCard(sport, m, { view: "deck" }));
    if (st.players?.length) {
      const pass = leadersRows(st.players, "passing", "pass yds")[0];
      const rush = leadersRows(st.players, "rushing", "rush yds")[0];
      const rec = leadersRows(st.players, "receiving", "rec yds")[0];
      const rows = [pass, rush, rec].filter(Boolean);
      if (rows.length) {
        const max = Math.max(...rows.map((r) => r.raw));
        cards.push(
          barsCard(sport, {
            view: "deck",
            tag: "Player",
            title: "Projection leaders",
            rows: rows.map(({ raw, ...r }) => ({ ...r, width: (raw / max) * 100 })),
            foot: `${st.players.length} player projections ${st.week ? `in Week ${st.week}` : "this week"}`,
          }),
        );
      }
      const td = st.players.filter((p) => Number.isFinite(p.tdProb)).sort((a, b) => b.tdProb - a.tdProb).slice(0, 3);
      if (td.length && sport === "cfb") {
        cards.push(
          barsCard(sport, {
            view: "deck",
            tag: "Player",
            title: `Anytime TD — Week ${st.week ?? ""} likeliest`.trim(),
            rows: td.map((p) => ({ name: p.name, sub: `${p.team} · ${p.pos}`, value: pct(p.tdProb), width: (p.tdProb / td[0].tdProb) * 100 })),
            foot: `${st.players.length} player projections published in Week ${st.week}`,
          }),
        );
      }
    }
    const cal = calibrationCard(sport, record?.perf?.calibration, record?.note || "");
    if (cal) cards.push(cal);
    const trend = record?.perf?.trend || [];
    if (sport === "cfb" && trend.length >= 2) {
      cards.push({
        sport,
        view: "deck",
        kind: "spark",
        tag: "CFB · Performance",
        time: "Season · weekly",
        title: "Moneyline hit rate by week",
        spark: sparkPoints(trend.map((t) => t.value)),
        legend: [trend[0], trend[trend.length - 1]].filter(Boolean).map((t) => `${t.label} ${pct(t.value)}`),
        foot: "Pre-kickoff subset where the API splits it",
      });
    }
    if (sport === "cfb" && !(st.matches || []).length) {
      cards.push({
        sport,
        view: "deck",
        kind: "note",
        tag: "CFB · Next up",
        time: st.week ? `Week ${st.week + 1}` : "",
        title: "The next slate",
        note: `Week ${st.week ?? ""} is graded and in the track record. The next slate's picks publish as the schedule rolls — every one made before kickoff, as always.`,
        foot: "Slate refreshes when the API rolls the week",
      });
    }
    return cards;
  }
  if (sport === "nba") {
    const rest = restMatches.slice(0, 3);
    for (const m of rest) cards.push(matchCard("nba", m, { view: "deck" }));
    // The featured player card: the top projection in the earliest game
    // not already leading the All board.
    const gameIds = Object.keys(st.players || {});
    for (const gid of gameIds.slice(0, 2)) {
      const p = st.players[gid][0];
      const m = (st.matches || []).find((x) => String(x.id) === String(gid));
      if (!p) continue;
      cards.push({
        sport,
        view: "deck",
        kind: "player",
        tag: "NBA · Player",
        time: "",
        startISO: m?.start,
        title: `${p.name} — projected line`,
        stats: [
          { k: "Points", v: num1(p.points) },
          { k: "Rebounds", v: num1(p.rebounds) },
          { k: "Assists", v: num1(p.assists) },
        ],
        note: m ? `${p.team} in ${m.awayName} @ ${m.homeName}.` : "",
        foot: "Player projections, made before tip-off",
      });
      break;
    }
    const buckets = record?.perf?.buckets || [];
    if (buckets.length) {
      cards.push({
        sport,
        view: "deck",
        kind: "buckets",
        tag: "NBA · Performance",
        time: "Confidence buckets · H2H",
        title: "What the confidence buckets deliver",
        buckets: buckets
          .filter((b) => b.total_predictions > 0)
          .map((b) => ({ bv: pct(b.hit_rate), h: Math.max(12, Math.round(b.hit_rate * 76)), bl: `${b.bucket}\nn=${b.total_predictions}` })),
        foot: "Pre-tip picks only",
      });
    }
    if (nbaCal?.length) {
      cards.push({
        sport,
        view: "deck",
        kind: "calib",
        tag: "NBA · Performance",
        time: `Calibration · win prob · n=${nbaCal.reduce((a, b) => a + (b.n || 0), 0)}`,
        title: "Calibration, un-smoothed",
        calib: calibPoints(nbaCal),
        legend: ["Dashed: perfect", "Orange: actual"],
        note: "Strong in the middle, inverted at the confident tail — drawn as served. The graph stays on the page until the model earns it off.",
        foot: "From /nba/calibration, all pre-tip game outcomes",
      });
    }
    if (st.rankings?.length) {
      cards.push({
        sport,
        view: "deck",
        kind: "table",
        tag: "NBA · Rankings",
        time: "Power top 5",
        title: "Power rankings — top of the board",
        rows: st.rankings.slice(0, 5).map((r) => ({ name: `${r.rank} · ${fullName(NBA_NAMES, r.abbreviation)}`, sub: "", value: num1(r.power_rating) })),
        foot: "Model power ratings",
      });
    }
    return cards;
  }
  if (sport === "f1") {
    if (st.drivers?.length) {
      cards.push(
        barsCard("f1", {
          view: "deck",
          tag: "Full field",
          wide: true,
          time: "",
          startISO: st.race?.race_datetime,
          title: "Win probability — the top of the grid",
          rows: st.drivers.slice(0, 5).map((d) => ({
            name: d.name,
            sub: `${d.constructor} · podium ${pct(d.podium)}`,
            value: pct(d.win),
            width: (d.win / st.drivers[0].win) * 100,
          })),
          foot: `${st.tier ? `${String(st.tier).replace(/_/g, " ")} tier` : "Pre-session"} · expected finish: ${st.drivers[0].name} ${num1(st.drivers[0].expPos)}`,
        }),
      );
      cards.push(
        barsCard("f1", {
          view: "deck",
          tag: "Podium",
          time: "",
          startISO: st.race?.race_datetime,
          title: `Podium — most likely at the ${String(st.race?.race_name || "next race").replace(/ Grand Prix$/, "")} GP`,
          rows: st.drivers
            .slice()
            .sort((a, b) => (b.podium ?? 0) - (a.podium ?? 0))
            .slice(0, 3)
            .map((d) => ({ name: d.name, sub: d.constructor, value: pct(d.podium), width: ((d.podium ?? 0) / (st.drivers[0].podium || 1)) * 100 })),
          foot: "Podium probabilities, same model run",
        }),
      );
    }
    const buckets = record?.perf?.buckets || [];
    if (buckets.length) {
      cards.push({
        sport,
        view: "deck",
        kind: "buckets",
        tag: "F1 · Performance",
        time: "Points-finish buckets",
        title: "When it calls a points finish",
        buckets: buckets.map((b) => ({
          bv: pct(b.n ? b.correct / b.n : null),
          h: Math.max(12, Math.round((b.n ? b.correct / b.n : 0) * 76)),
          bl: `${b.bucket}\n${b.correct}/${b.n}`,
        })),
        foot: `${record.perf.nResolved ?? ""} resolved picks · ${record.perf.nPre ?? ""} pre-session`.trim(),
      });
    }
    return cards;
  }
  return cards;
}

// --- KPIs ------------------------------------------------------------------

/** The All board's KPI strip, computed from the same states as the cards:
 *  events today, the most confident call, the biggest gap, the first
 *  event, and how many models answered. A chip that cannot be computed is
 *  omitted — never estimated. */
export function buildKpis(states, now = new Date()) {
  const kpis = [];
  const todayStr = ctDay(now);
  let eventsToday = 0;
  let first = null;
  let live = 0;
  for (const st of Object.values(states || {})) {
    if (!st || st.failed) continue;
    live += 1;
    const starts = [
      ...(st.matches || []).filter((m) => !m.late).map((m) => m.start),
      ...(st.sport === "f1" && st.race ? [st.race.race_datetime] : []),
    ];
    for (const s of starts) {
      const d = new Date(s);
      if (Number.isNaN(d.getTime())) continue;
      if (ctDay(d) === todayStr) {
        eventsToday += 1;
        if (!first || d < first.d) first = { d, sport: st.sport };
      }
    }
  }
  if (eventsToday) kpis.push({ b: String(eventsToday), t: "events today" });
  let hero = null;
  for (const st of Object.values(states || {})) {
    if (!st || st.failed) continue;
    for (const m of st.matches || []) {
      if (m.late) continue;
      if (!hero || m.pickProb > hero.m.pickProb) hero = { sport: st.sport, m };
    }
  }
  if (hero) kpis.push({ b: pct(hero.m.pickProb), t: `most confident call · ${pickName(hero.m)}` });
  const consensus = bestConsensus(states, hero);
  if (consensus) kpis.push({ b: pct(consensus.m.pickProb), t: `model and market agree · ${consensus.m.away} @ ${consensus.m.home}` });
  if (first) kpis.push({ b: fmtTime(first.d.toISOString()), t: "first event today" });
  kpis.push({ b: String(live), t: live === 5 ? "models live" : "of 5 models live" });
  return kpis;
}

// --- painting --------------------------------------------------------------
//
// Text only, as in teasers.js: every string comes from an API payload, so
// nothing here uses innerHTML. A card is one <a> — the whole card is the
// link to the sport's page, the way the mockup specified and the way the
// old .sport cards behaved.

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function paintCard(card, now) {
  const meta = SPORTS_META[card.sport];
  const a = el("a", `card${card.wide ? " wide" : ""}${card.notch ? " pr-notch" : ""}`);
  a.href = meta.site;
  a.dataset.sport = card.sport;
  const head = el("div", "chead");
  const stag = el("span", "stag");
  const img = el("img", "fav");
  img.src = meta.favicon;
  img.alt = "";
  stag.append(img, document.createTextNode(card.tag));
  head.append(stag);
  const when = card.startISO ? fmtWhen(card.startISO, now) : card.time || "";
  if (when) head.append(el("span", "ctime", when));
  a.append(head);

  if (card.kind === "empty" || card.kind === "note") {
    a.append(el("h3", null, card.title));
    if (card.note) a.append(el("p", "empty-note", card.note));
  } else {
    const h3 = el("h3", null, card.title);
    if (card.band) {
      const band = el("span", `band${card.band === "Moderate" ? " mod" : card.band === "Leaning" ? " lean" : ""}`, card.band);
      h3.append(document.createTextNode(" "), band);
    }
    a.append(h3);
  }

  if (card.big) {
    const row = el("div", "bigrow");
    row.append(el("span", `bigpct${card.bigWin ? " win" : ""}`, card.big));
    if (card.lab?.length) {
      const lab = el("span", "biglab");
      card.lab.forEach((line, i) => {
        if (i) lab.append(document.createElement("br"));
        lab.append(document.createTextNode(line));
      });
      row.append(lab);
    }
    a.append(row);
  }
  if (card.segments) {
    const bar = el("div", "splitbar");
    for (const s of card.segments) {
      const i = document.createElement("i");
      i.style.width = `${Math.max(0, Math.min(100, s.w))}%`;
      i.style.background = s.color;
      bar.append(i);
    }
    a.append(bar);
  }
  if (card.meter) {
    const meter = el("div", "meter");
    const fill = el("div", "fill");
    fill.style.width = `${Math.max(0, Math.min(100, card.meter.fill))}%`;
    const mark = el("div", "mark");
    mark.style.left = `${Math.max(0, Math.min(100, card.meter.mark))}%`;
    meter.append(fill, mark);
    a.append(meter);
  }
  if (card.legend?.length) {
    const legend = el("div", "legend");
    for (const t of card.legend) legend.append(el("span", null, t));
    a.append(legend);
  }
  if (card.rows?.length) {
    const rows = el("div", "rows");
    for (const r of card.rows) {
      const prow = el("div", "prow");
      const nm = el("span", "nm");
      nm.append(document.createTextNode(r.name));
      if (r.sub) {
        const sub = el("span", null, ` ${r.sub}`);
        nm.append(sub);
      }
      prow.append(nm, el("span", "vl", r.value));
      if (typeof r.width === "number") {
        const bar = el("div", "bar");
        const i = document.createElement("i");
        i.style.width = `${Math.max(0, Math.min(100, r.width))}%`;
        bar.append(i);
        prow.append(bar);
      }
      rows.append(prow);
    }
    a.append(rows);
  }
  if (card.stats?.length) {
    const grid = el("div", "statgrid");
    for (const s of card.stats) {
      const sg = el("div", "sg");
      sg.append(el("div", "k", s.k), el("div", "v", s.v));
      grid.append(sg);
    }
    a.append(grid);
  }
  if (card.spark) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 100 36");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", card.spark);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "var(--color-pr-accent)");
    line.setAttribute("stroke-width", "2");
    svg.append(line);
    a.append(svg);
    if (card.legend?.length) {
      const legend = el("div", "legend");
      for (const t of card.legend) legend.append(el("span", null, t));
      a.append(legend);
    }
  }
  if (card.calib) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 100 64");
    svg.setAttribute("style", "height:5.375rem");
    svg.setAttribute("aria-hidden", "true");
    const diag = document.createElementNS("http://www.w3.org/2000/svg", "line");
    diag.setAttribute("x1", "0");
    diag.setAttribute("y1", "64");
    diag.setAttribute("x2", "100");
    diag.setAttribute("y2", "0");
    diag.setAttribute("stroke", "var(--color-pr-fill-mute)");
    diag.setAttribute("stroke-width", "1");
    diag.setAttribute("stroke-dasharray", "3 3");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", card.calib);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "var(--color-pr-accent)");
    line.setAttribute("stroke-width", "2");
    svg.append(diag, line);
    a.append(svg);
    if (card.legend?.length) {
      const legend = el("div", "legend");
      for (const t of card.legend) legend.append(el("span", null, t));
      a.append(legend);
    }
  }
  if (card.buckets?.length) {
    const wrap = el("div", "buckets");
    for (const b of card.buckets) {
      const bucket = el("div", "bucket");
      bucket.append(el("span", "bv", b.bv));
      const col = el("div", "col");
      col.style.height = `${b.h}%`;
      bucket.append(col);
      const bl = el("span", "bl");
      String(b.bl).split("\n").forEach((part, i) => {
        if (i) bl.append(document.createElement("br"));
        bl.append(document.createTextNode(part));
      });
      bucket.append(bl);
      wrap.append(bucket);
    }
    a.append(wrap);
  }
  if (card.pills?.length) {
    const pills = el("div", "pills");
    for (const p of card.pills) pills.append(el("span", `pill${p.cls ? ` ${p.cls}` : ""}`, p.t));
    a.append(pills);
  }
  if (card.note && card.kind !== "note") a.append(el("p", "note", card.note));
  const foot = el("div", "cfoot");
  foot.append(el("span", null, card.foot || ""), el("span", "open", meta.open));
  a.append(foot);
  return a;
}

function paintDeckHead(container, head) {
  if (!container) return;
  container.replaceChildren();
  if (!head) return;
  const meta = SPORTS_META[head.sport];
  const wrap = el("div", "deckhead");
  wrap.dataset.sport = head.sport;
  const left = el("div");
  const t = el("div", "t");
  const img = el("img", "fav");
  img.src = meta.favicon;
  img.alt = "";
  t.append(img, document.createTextNode(head.title));
  left.append(t);
  if (head.desc) left.append(el("div", "d", head.desc));
  const big = el("div", "big", head.big);
  big.append(el("small", null, head.small));
  wrap.append(left, big);
  container.append(wrap);
}

// --- cache -----------------------------------------------------------------
//
// Stale-while-revalidate storage. One localStorage entry per endpoint
// URL under a versioned namespace: { t: fetched-at epoch ms, body: the
// parsed JSON }. Only successful, complete responses are written — the
// write happens in the network getter below, after response.json() has
// resolved — and a payload whose serialized entry exceeds ~800 KB is
// skipped (the NBA track record ships every per-pick row, several MB;
// it simply revalidates each visit instead). Every storage touch is
// wrapped: quota errors and privacy modes disable the cache silently
// and must never break rendering. The helpers are pure over a
// localStorage-shaped `store` ({ getItem, setItem, removeItem }) so the
// test suite can drive them with a fake; a null store is a disabled
// cache.

export const CACHE_NS = "predictor-home-v1";
export const CACHE_MAX_CHARS = 800_000;

export const cacheKey = (url) => `${CACHE_NS}:${url}`;

/** The cached entry for a URL: { t, body }, or null when there is none,
 *  when it is malformed, or when storage itself is unavailable. */
export function readCacheEntry(store, url) {
  if (!store) return null;
  try {
    const raw = store.getItem(cacheKey(url));
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry || typeof entry.t !== "number" || !("body" in entry)) return null;
    return entry;
  } catch {
    return null;
  }
}

/** Cache a successful payload. Returns false — and stores nothing —
 *  when the entry is oversized or storage refuses it. */
export function writeCacheEntry(store, url, body, now = Date.now()) {
  if (!store) return false;
  try {
    const raw = JSON.stringify({ t: now, body });
    if (raw.length > CACHE_MAX_CHARS) return false;
    store.setItem(cacheKey(url), raw);
    return true;
  } catch {
    return false;
  }
}

/** The snapshot timestamp a snapshot-meta payload carries: its
 *  generated_at, verbatim. A live meta (null/absent generated_at) and a
 *  malformed one both yield null — "no stamp" can never prove a sport
 *  unchanged, so it always revalidates. */
export function metaStamp(meta) {
  const at = meta?.generated_at;
  if (typeof at !== "string" || !at) return null;
  return Number.isNaN(new Date(at).getTime()) ? null : at;
}

/** True only when both metas carry the same snapshot timestamp — the
 *  one case where a sport's heavy endpoints may be skipped entirely. */
export function metaMatches(cachedMeta, freshMeta) {
  const a = metaStamp(cachedMeta);
  const b = metaStamp(freshMeta);
  return a !== null && b !== null && a === b;
}

/** The browser's localStorage, or null where it cannot even be touched
 *  (privacy modes throw on access in some engines). */
function defaultStore() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

// --- fetching --------------------------------------------------------------

const settle = (p) => p.then((v) => ({ ok: true, v }), () => ({ ok: false, v: null }));

/** The two getters a boot runs the loaders through. `netGet` is the
 *  network with write-through caching: a non-OK response throws and is
 *  never cached. `cacheGet` serves only what a previous visit cached
 *  and throws on a miss, so a loader run through it fails exactly the
 *  way a network loader fails — the normalizers and builders downstream
 *  are identical for both passes. */
function makeGetters(store) {
  const netGet = async (path) => {
    const response = await fetch(path, { headers: { accept: "application/json" } });
    if (!response.ok) throw new Error(`${path} responded ${response.status}`);
    const body = await response.json();
    writeCacheEntry(store, path, body);
    return body;
  };
  const cacheGet = async (path) => {
    const entry = readCacheEntry(store, path);
    if (!entry) throw new Error(`${path} not cached`);
    return entry.body;
  };
  return { netGet, cacheGet };
}

async function loadPL(get, { players = true } = {}) {
  const [gw, table] = await Promise.all([get("/pl/api/fixtures/gameweek"), settle(get("/pl/api/hub/table"))]);
  const playersByFixture = {};
  if (players) {
    const upcoming = (gw?.fixtures || []).filter((f) => !f.finished).slice(0, 2);
    await Promise.all(
      upcoming.map(async (f) => {
        const r = await settle(get(`/pl/api/fixtures/${encodeURIComponent(f.event_id)}/players`));
        if (r.ok) playersByFixture[f.event_id] = r.v;
      }),
    );
  }
  return normalizePL({ gw, playersByFixture, table: table.ok ? table.v : null });
}

async function loadFootball(sport, get) {
  const base = `/${sport}`;
  const week = await get(`${base}/api/current-week`);
  const [games, preds, props] = await Promise.all([
    get(`${base}/api/games?season=${week.season}&week=${week.week}`),
    get(`${base}/api/predictions/${week.season}/${week.week}/batch`),
    settle(get(`${base}/api/players/${week.season}/${week.week}/props`)),
  ]);
  return normalizeFootball(sport, { week, games, preds, props: props.ok ? props.v : [] });
}

async function loadNBA(get, { players = true } = {}) {
  const today = new Date().toISOString().slice(0, 10);
  const rankingsP = settle(get("/nba/hub/rankings"));
  const games = await get(`/nba/games/week?start=${today}`);
  const playersByGame = {};
  if (players) {
    const upcoming = (Array.isArray(games) ? games : []).filter((g) => !g.completed).slice(0, 4);
    await Promise.all(
      upcoming.map(async (g) => {
        const r = await settle(get(`/nba/games/${encodeURIComponent(g.game_id)}/players`));
        if (r.ok) playersByGame[g.game_id] = r.v;
      }),
    );
  }
  const rankings = await rankingsP;
  return normalizeNBA({ games, playersByGame, rankings: rankings.ok ? rankings.v : [] });
}

async function loadF1(get) {
  const races = await get("/f1/api/races");
  const race = (races || []).filter((r) => !r.completed)[0];
  let pred = null;
  if (race) {
    const r = await settle(get(`/f1/api/races/${race.season}/${race.round}/prediction`));
    if (r.ok) pred = r.v;
  }
  return normalizeF1({ races, pred });
}

export function loadSport(sport, get, opts) {
  if (sport === "pl") return loadPL(get, opts);
  if (sport === "nfl" || sport === "cfb") return loadFootball(sport, get);
  if (sport === "nba") return loadNBA(get, opts);
  return loadF1(get);
}

/** The deferred players load for one already-loaded sport: the same
 *  per-fixture / per-game fan-out the full load runs, over the same
 *  caps (PL's next two fixtures, NBA's next four games), returning the
 *  pivoted players map for `state.sports[sport].players`. Only PL and
 *  NBA fan out per game; football's props are one batch call already
 *  in its picks load, and F1 has no player payloads. */
export async function loadPlayers(sport, get, st) {
  if (!st || st.failed) return {};
  if (sport === "pl") {
    const byFixture = {};
    await Promise.all(
      (st.matches || []).slice(0, 2).map(async (m) => {
        const r = await settle(get(`/pl/api/fixtures/${encodeURIComponent(m.id)}/players`));
        if (r.ok) byFixture[m.id] = r.v;
      }),
    );
    return plPlayers(st.matches, byFixture);
  }
  if (sport === "nba") {
    const byGame = {};
    await Promise.all(
      (st.matches || []).slice(0, 4).map(async (m) => {
        const r = await settle(get(`/nba/games/${encodeURIComponent(m.id)}/players`));
        if (r.ok) byGame[m.id] = r.v;
      }),
    );
    return nbaPlayers(byGame);
  }
  return {};
}

const RECORD_PATHS = {
  pl: "/pl/api/hub/track-record",
  nfl: "/nfl/api/track-record",
  cfb: "/cfb/api/track-record",
  nba: "/nba/hub/track-record",
  f1: "/f1/api/track-record",
};
// PL has no snapshot-meta route (404, confirmed) — it revalidates on
// every visit; its endpoints are the family's lightest.
const META_PATHS = { nfl: "/nfl/api/snapshot-meta", cfb: "/cfb/api/snapshot-meta", nba: "/nba/snapshot-meta", f1: "/f1/api/snapshot-meta" };

/** One sport's record through a getter; null when the payload carries
 *  nothing the deck can frame (or the getter failed — callers settle). */
async function loadRecord(sport, get) {
  return normalizeRecord(sport, await get(RECORD_PATHS[sport]));
}

// --- boot ------------------------------------------------------------------

const state = { sports: {}, records: {}, nbaCal: [], filter: "all", fresh: {}, meta: {} };

// What the last render put on the page, kept so a background patch can
// preserve the DOM nodes of cards whose content did not change:
// card key -> { hash, node }, plus the KPI and deck-head signatures.
const painted = { nodes: new Map(), filter: null, kpiSig: null, headSig: null };

const sigOf = (x) => {
  try {
    return JSON.stringify(x) ?? "";
  } catch {
    return "";
  }
};

function allCards() {
  return buildAllCards(state.sports);
}

function usedMatchIds(sport, cards) {
  return new Set(cards.filter((c) => c.sport === sport && c.matchId != null).map((c) => String(c.matchId)));
}

const cardKey = (card) => `${card.sport}|${card.view}|${card.kind}|${card.matchId ?? ""}|${card.title ?? ""}`;

function paintFreshness() {
  const fresh = document.querySelector("#freshness");
  if (!fresh) return;
  const bits = SPORT_ORDER.filter((s) => state.fresh[s]).map((s) => `${SPORTS_META[s].short}: ${state.fresh[s].toLowerCase()}`);
  const text = bits.length ? `Freshness — ${bits.join(" · ")}.` : "";
  if (fresh.textContent !== text) fresh.textContent = text;
}

/** Paint the desired card items into the grid. With `reconcile`, an
 *  item whose key and content hash survive from the previous render
 *  keeps its exact DOM node, and when the resulting node sequence is
 *  already what is on the page the grid is not touched at all — a
 *  background update for one sport cannot disturb the reader's place
 *  in another's cards. */
function paintItems(grid, items, reconcile) {
  const prev = reconcile && painted.filter === state.filter ? painted.nodes : null;
  const next = new Map();
  const nodes = items.map((it) => {
    const kept = prev?.get(it.key);
    if (kept && kept.hash === it.hash) {
      next.set(it.key, kept);
      return kept.node;
    }
    const node = it.paint();
    next.set(it.key, { hash: it.hash, node });
    return node;
  });
  const unchanged = grid.children.length === nodes.length && nodes.every((n, i) => grid.children[i] === n);
  if (!unchanged) grid.replaceChildren(...nodes);
  painted.nodes = next;
  painted.filter = state.filter;
}

function render({ reconcile = false } = {}) {
  const now = new Date();
  const grid = document.querySelector("#grid");
  const headBox = document.querySelector("#deckhead");
  const kpisBox = document.querySelector("#kpis");
  if (!grid) return;
  const cards = allCards();
  // KPIs belong to the All board; a filtered deck keeps its own header.
  // The strip only repaints when its content actually changed, so a
  // background patch never reflows it under the reader.
  if (kpisBox) {
    const kpis = state.filter === "all" ? buildKpis(state.sports, now) : [];
    const ksig = sigOf(kpis);
    if (ksig !== painted.kpiSig) {
      painted.kpiSig = ksig;
      kpisBox.replaceChildren();
      for (const k of kpis) {
        const chip = el("span", "kpi");
        chip.append(el("b", null, k.b), document.createTextNode(k.t));
        kpisBox.append(chip);
      }
    }
    kpisBox.setAttribute("aria-busy", "false");
  }
  const toItem = (card) => ({ key: cardKey(card), hash: sigOf(card), paint: () => paintCard(card, now) });
  const noPicks = () => ({ key: "__note__", hash: "no-picks", paint: () => el("p", "empty-note", "No picks yet — check back soon.") });
  let items;
  let head = null;
  if (state.filter === "all") {
    items = cards.length ? cards.map(toItem) : [noPicks()];
  } else {
    const sport = state.filter;
    const st = state.sports[sport];
    head = st && !st.failed ? deckHead(sport, st, state.records[sport]) : null;
    if (!st || st.failed) {
      const empty = {
        sport,
        kind: "empty",
        tag: `${SPORTS_META[sport].short} · Picks`,
        title: "Couldn't load picks.",
        note: "No picks yet — check back soon.",
        foot: "",
      };
      items = [toItem(empty)];
    } else {
      const mine = cards.filter((c) => c.sport === sport);
      const deck = buildDeckCards(sport, st, state.records[sport], state.nbaCal, usedMatchIds(sport, cards));
      const shown = [...mine, ...deck];
      items = shown.length ? shown.map(toItem) : [noPicks()];
    }
  }
  const hsig = sigOf(head);
  if (hsig !== painted.headSig) {
    painted.headSig = hsig;
    paintDeckHead(headBox, head);
  }
  paintItems(grid, items, reconcile);
  grid.setAttribute("aria-busy", "false");
  paintFreshness();
}

/** A background revalidation landed new data for one sport. Repaint
 *  only what that sport can affect — in a deck view for another sport,
 *  nothing on the page depends on it — and always by reconciliation, so
 *  the filter selection and the reader's scroll are never touched. */
function patchSport(sport) {
  if (state.filter !== "all" && state.filter !== sport) return;
  render({ reconcile: true });
}

function applyFilter(f, push = true) {
  state.filter = ["pl", "nfl", "cfb", "nba", "f1"].includes(f) ? f : "all";
  document.querySelectorAll(".fbtn").forEach((b) => b.classList.toggle("on", b.getAttribute("data-filter") === state.filter));
  if (push) {
    try {
      history.replaceState(null, "", state.filter === "all" ? "#" : `#${state.filter}`);
    } catch {
      /* a file:// preview has no history to keep */
    }
  }
  render();
}

async function boot() {
  const hash = (location.hash || "").replace("#", "");
  if (["pl", "nfl", "cfb", "nba", "f1"].includes(hash)) state.filter = hash;
  document.querySelectorAll(".fbtn").forEach((b) => {
    b.classList.toggle("on", b.getAttribute("data-filter") === state.filter);
    b.addEventListener("click", () => applyFilter(b.getAttribute("data-filter")));
  });
  window.addEventListener("hashchange", () => applyFilter((location.hash || "").replace("#", ""), false));

  const { netGet, cacheGet } = makeGetters(defaultStore());

  // Pass 0 — the cache. Whatever a previous visit left behind paints
  // immediately, normalized and built by the same code that handles
  // fresh data, so a cached pick is held to exactly the same rules.
  // A sport the cache cannot fully serve stays out of this paint;
  // revalidation fills it in below. With no cache at all, nothing
  // paints here and the first visit behaves exactly as it always has.
  let cachePainted = false;
  await Promise.all(
    SPORT_ORDER.map(async (sport) => {
      const [st, rec, meta] = await Promise.all([
        settle(loadSport(sport, cacheGet)),
        settle(loadRecord(sport, cacheGet)),
        META_PATHS[sport] ? settle(cacheGet(META_PATHS[sport])) : Promise.resolve({ ok: false, v: null }),
      ]);
      if (st.ok) {
        state.sports[sport] = st.v;
        cachePainted = true;
      }
      if (rec.ok && rec.v) state.records[sport] = rec.v;
      if (meta.ok) {
        state.meta[sport] = meta.v;
        const f = freshness(meta.v);
        if (f) state.fresh[sport] = f;
      }
    }),
  );
  const cal0 = await settle(cacheGet("/nba/calibration"));
  if (cal0.ok) state.nbaCal = normalizeNBAcalibration(cal0.v);
  if (cachePainted) render();
  let painted = cachePainted;

  // Wave A — the picks, revalidated sport by sport. A sport with a
  // snapshot-meta whose generated_at still matches the cached one is
  // skipped outright: its cached picks already are the current ones.
  // PL has no meta and always revalidates. A sport whose data moved is
  // patched in place; a sport the network cannot reach keeps whatever
  // the cache painted, with its as-of line.
  const metaVerified = {};
  await Promise.all(
    SPORT_ORDER.map(async (sport) => {
      if (META_PATHS[sport]) {
        const m = await settle(netGet(META_PATHS[sport]));
        if (m.ok) {
          const f = freshness(m.v);
          if (f && f !== state.fresh[sport]) {
            state.fresh[sport] = f;
            paintFreshness();
          }
          const unchanged = metaMatches(state.meta[sport], m.v);
          state.meta[sport] = m.v;
          if (unchanged && state.sports[sport] && !state.sports[sport].failed) {
            metaVerified[sport] = true;
            return;
          }
        } else if (state.sports[sport] && !state.sports[sport].failed) {
          // Offline, with a cached board for this sport already up:
          // it stays as it is rather than being marked failed.
          metaVerified[sport] = true;
          return;
        }
      }
      const before = sigOf(state.sports[sport]);
      try {
        // Picks only — the per-game player fan-out is deferred to
        // Wave B. First paint used to wait for every sport's FULL
        // load (five loaders, two tiers each, the slowest sport
        // gating the whole board), which is why a cold visit showed
        // no picks for seconds. Now the first sport to land paints
        // and the rest patch in as they arrive, the same patching
        // the cache path already used.
        state.sports[sport] = await loadSport(sport, netGet, { players: false });
      } catch (err) {
        if (!state.sports[sport]) {
          state.sports[sport] = { sport, failed: true, matches: [], players: {} };
        }
        console.warn(`hub home ${sport} failed`, err);
      }
      if (!painted) {
        render();
        painted = true;
      } else if (sigOf(state.sports[sport]) !== before) patchSport(sport);
    }),
  );
  if (!painted) {
    render();
    painted = true;
  }

  // Wave B — the records, the NBA calibration curve, and the deferred
  // player payloads. They only feed deck headers, performance cards
  // and the player stories, so they must never delay the board — the
  // NBA track record in particular ships every per-pick row it has
  // (and so is never cached; see the size cap). A sport whose snapshot
  // verified unchanged in Wave A keeps its cached record — and its
  // cached players — when it has them; anything else fetches fresh
  // and patches.
  await Promise.all([
    ...SPORT_ORDER.map(async (sport) => {
      if (metaVerified[sport] && state.records[sport]) return;
      const before = sigOf(state.records[sport]);
      const r = await settle(loadRecord(sport, netGet));
      if (r.ok && r.v) {
        state.records[sport] = r.v;
        if (painted && sigOf(r.v) !== before) patchSport(sport);
      }
    }),
    ...SPORT_ORDER.map(async (sport) => {
      if (metaVerified[sport]) return;
      const st = state.sports[sport];
      if (!st || st.failed) return;
      const r = await settle(loadPlayers(sport, netGet, st));
      if (r.ok && r.v && Object.keys(r.v).length) {
        st.players = r.v;
        if (painted) patchSport(sport);
      }
    }),
    (async () => {
      if (metaVerified.nba && state.nbaCal.length) return;
      const r = await settle(netGet("/nba/calibration"));
      if (r.ok) {
        const cal = normalizeNBAcalibration(r.v);
        if (sigOf(cal) !== sigOf(state.nbaCal)) {
          state.nbaCal = cal;
          if (painted) patchSport("nba");
        }
      }
    })(),
  ]);
  if (!painted) render();
}

// Guarded so the Node test suite can import the pure builders without a
// DOM, exactly as teasers.js is guarded.
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
}
