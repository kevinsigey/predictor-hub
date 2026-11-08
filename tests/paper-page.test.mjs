import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The paper-trading page. It is the one page in the hub that shows the
// ledger's own evidence, so the things it asserts about itself are about
// honesty rather than looks: no segment may be called "has an edge" below the
// gate, and every figure a reader sees has to be one the report actually
// carries.
const html = readFileSync(new URL("../paper.html", import.meta.url), "utf8");
const report = JSON.parse(
  readFileSync(new URL("../data/paper/report.json", import.meta.url), "utf8")
);

test("the page shows per-sport rows and one segment table", () => {
  assert.match(html, /id="sport-table"/);
  assert.match(html, /id="segment-table"/);
  // ONE table of segments: a second one would mean two places to read, and
  // the spec asks for one table with a sport filter.
  assert.equal((html.match(/id="segment-table"/g) || []).length, 1);
});

test("the segment table can be filtered by sport", () => {
  assert.match(html, /class="sport-filter"/);
  assert.match(html, /data-sport-filter="all"/);
  // The chips are buttons with an accessible group label, not divs.
  assert.match(html, /role="group" aria-label="Filter by sport"/);
  assert.match(html, /<button type="button" class="sport-chip/);
});

test("the filter hides rows rather than removing them", () => {
  // Removing rows would lose the reader's place and change the table's shape
  // as they filter.
  assert.match(html, /classList\.toggle\("is-hidden"/);
  assert.match(html, /tr\.is-hidden\s*\{\s*display:\s*none/);
});

test("the report carries the per-sport rows the page reads", () => {
  assert.ok(report.per_sport, "report.json has no per_sport");
  for (const [sport, row] of Object.entries(report.per_sport)) {
    assert.equal(row.sport, sport);
    for (const key of ["n_logged", "n_shadow", "n_settled", "pnl",
                       "mean_clv_cents", "n_clv", "share_clv_positive",
                       "excluded_legacy_total_rows", "qualifies_for_edge_claim",
                       "n_bets", "n_games", "n_settled_bets", "n_settled_games"]) {
      assert.ok(key in row, `${sport} row has no ${key}`);
    }
  }
});

test("the page shows the games as well as the bets", () => {
  // A segment's bets are not independent of each other -- the rungs of one
  // game move together -- so the page has to show both counts or the reader
  // cannot tell how much evidence there is.
  assert.match(html, /n_bets/);
  assert.match(html, /n_games/);
  assert.match(html, />Games <span class="label-paper">/);
});

test("the report's games count is never more than its bets count", () => {
  // A game holds at least one bet, so the game count cannot exceed the bet
  // count. If it does, the two are being counted over different populations.
  for (const [sport, row] of Object.entries(report.per_sport || {})) {
    assert.ok(row.n_games <= row.n_bets, `${sport}: ${row.n_games} games > ${row.n_bets} bets`);
    assert.ok(row.n_settled_games <= row.n_settled_bets,
              `${sport}: ${row.n_settled_games} settled games > ${row.n_settled_bets} settled bets`);
  }
});

test("the gate carries the second floor on games", () => {
  assert.equal(report.gate.min_settled_games, 100);
  // The first floor is untouched: this adds to it, it does not replace it.
  assert.equal(report.gate.min_settled, 300);
});

test("no sport below the gate is called as having an edge", () => {
  for (const [sport, row] of Object.entries(report.per_sport || {})) {
    if (row.qualifies_for_edge_claim) continue;
    assert.equal(row.has_edge_claim, false, `${sport} is below the gate but claims an edge`);
    assert.ok(row.why_not_yet, `${sport} is below the gate but gives no reason`);
  }
});

test("a sport below the gate says why in plain words", () => {
  for (const [sport, row] of Object.entries(report.per_sport || {})) {
    if (row.qualifies_for_edge_claim) continue;
    const why = row.why_not_yet || "";
    assert.ok(why.length > 0, `${sport} gives no reason`);
    // A reason that is only the gate's internal wording is not plain words:
    // it has to say what is missing in the reader's terms.
    assert.ok(
      /settled bets|interval|calibration|shadow|model/i.test(why),
      `${sport} reason is not in plain words: ${why}`
    );
  }
});

test("the page states that CLV is a leading indicator and not a gate", () => {
  // The report's CLV is beside the result, and the page must not let a reader
  // take it for a gate condition.
  assert.match(html, /leading indicator/i);
});

test("the gate is what the page says it is", () => {
  const min = report.gate.min_settled;
  assert.equal(min, 300);
  // The gate floors come from the report at render time, and only ever in a
  // dropdown: they are no longer the page's headline.
  assert.match(html, /about " \+ minBets \+ " settled paper bets/);
  assert.match(html, /cannot be judged yet/i);
  assert.doesNotMatch(html, /Progress by sport|Log it|You are here/);
});

test("the page carries the excluded-legacy note", () => {
  const excluded = report.totals.excluded_legacy_total_rows || 0;
  if (excluded > 0) {
    assert.match(html, /earlier total-points rows excluded/i);
  }
});

// ---- Three-question layout ----
import vm from "node:vm";

const libSrc = html.match(/<script id="paper-lib">([\s\S]*?)<\/script>/)[1];
const sandbox = {};
vm.runInNewContext(libSrc, sandbox);
const Lib = sandbox.PaperLib;

test("the page has the three question sections, in order", () => {
  const ids = [...html.matchAll(/<section class="q" id="(q\d)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ["q1", "q2", "q3"]);
  assert.match(html, /Is the model better than the market\?/);
  assert.match(html, /Are we finding prices before the market\?/);
  assert.match(html, /Does it make money after fees\?/);
});

test("the page loads no external scripts but the existing analytics counter", () => {
  const srcs = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(srcs, ["//gc.zgo.at/count.js"]);
});

test("explanations live in dropdowns, not blocks of text", () => {
  const details = (html.match(/<details class="panel"/g) || []).length;
  assert.equal(details, 6);
  for (const title of ["How a pick is judged", "All numbers by sport", "Segments (sport x market x confidence band)",
                       "All-time vs current model", "Glossary"]) {
    assert.ok(html.includes(`<summary>${title}`), `missing dropdown ${title}`);
  }
  const outside = html.replace(/<details[\s\S]*?<\/details>/g, "").replace(/<script[\s\S]*?<\/script>/g, "");
  for (const m of outside.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/g)) {
    assert.ok(m[1].length <= 220, `long paragraph outside <details>: ${m[1].slice(0, 60)}`);
  }
});

test("the forest plot handles null CI, null n_clv and an empty report", () => {
  const dom = [-1, 1];
  assert.match(Lib.forest({ kind: "q1", est: 0.2, ci: null, n: 5, domain: dom }), /no interval/);
  assert.match(Lib.forest({ kind: "q2", est: null, ci: null, n: 0, domain: dom }), /no data yet/);
  assert.match(Lib.forest({ kind: "q1", est: 0.2, ci: [0.1, 0.3], n: 50, domain: dom }), /f-win/);
  assert.match(Lib.forest({ kind: "q1", est: -0.2, ci: [-0.3, -0.1], n: 50, domain: dom }), /f-loss/);
  assert.match(Lib.forest({ kind: "q1", est: 0, ci: [-0.1, 0.1], n: 50, domain: dom }), /f-lean/);
  const grey = Lib.forest({ kind: "q1", est: -0.09, ci: [-0.12, -0.06], n: 3, domain: dom, grey: true });
  assert.doesNotMatch(grey, /class="bar/);
  assert.match(grey, /n = 3/);
  assert.match(grey, /role="img" aria-label=/);
  for (const r of [undefined, null, {}, { scoring: {}, verdicts: {} }]) {
    const m = Lib.model(r);
    assert.equal(m.ready, r && r.scoring ? true : false);
    assert.equal(m.sports.length, 0);
    assert.ok(Lib.heroLine(m));
  }
});

test("a sport with no closing lines yet is pending, never a zero", () => {
  const m = Lib.model({ per_sport: { nba: { n_clv: 0, mean_clv_cents: null } } });
  assert.equal(m.sports[0].q2.pending, true);
  assert.match(html, /Capturing: first closes arrive on the next game day/);
});

test("status chips cover all four statuses", () => {
  for (const [k, text] of [["proven", "Proven"], ["leaning", "Leaning"], ["against", "Against"], ["not_yet", "Not yet"]]) {
    assert.match(Lib.chip(k), new RegExp(`class="chip ${k}">${text}<`));
  }
  assert.match(Lib.chip("bogus"), /Not yet/);
});

test("path-to-verdict wording is truthful", () => {
  assert.equal(Lib.needText(null, -0.03), "No path at the current result");
  assert.equal(Lib.needText(null, null), "No path at the current result");
  assert.equal(Lib.needText(1200, 0.02), "about 1,200 more bets at the current effect");
});

test("the real report renders: biggest sample first, no edge claimed", () => {
  const m = Lib.model(report);
  assert.equal(m.ready, true);
  const ns = Array.from(m.sports, (s) => s.n);
  assert.deepEqual(ns, [...ns].sort((a, b) => b - a));
  const f1 = m.sports.find((s) => s.key === "f1");
  assert.equal(f1.q1.grey, true);
  const line = Lib.heroLine(m);
  if (!m.sports.some((s) => s.q3.status === "proven")) assert.doesNotMatch(line, /proven|beat/i);
  assert.doesNotMatch(html, /Beats the market/);
});
