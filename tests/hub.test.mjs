import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const tokens = readFileSync(new URL("../packages/predictor-ui/src/tokens.css", import.meta.url), "utf8");
const HOST = "40-160-91-131\\.sslip\\.io";

test("links point at the VPS sites, not the retired Azure apps", () => {
  assert.doesNotMatch(html, /azurecontainerapps\.io/);
  for (const sub of ["pl", "f1", "nba"]) assert.match(html, new RegExp(`href="https://${sub}\\.${HOST}"`));
  for (const sport of ["nfl", "cfb"]) assert.match(html, new RegExp(`href="https://sports\\.${HOST}/\\?sport=${sport}"`));
});

test("carries the family frame: wordmark and a switcher to every sport", () => {
  assert.match(html, /<body data-sport="hub">/);
  assert.match(html, /<a[^>]*class="wordmark"[^>]*>Predictor<\/a>/);
  const nav = html.match(/<nav aria-label="Sports"[\s\S]*?<\/nav>/);
  assert.ok(nav, "sport switcher nav");
  // The Home tab leads the row (Kevin's call for the landing redesign,
  // 2026-11-01): it is the hub's own entry, marked current, ahead of the six
  // entries every surface shares. The wordmark remains the family name —
  // see the wordmark test below — and also carries aria-current.
  assert.deepEqual([...nav[0].matchAll(/>(\w+[\w ]*)<\/a>/g)].map((m) => m[1].trim()), [
    "Home",
    "Premier League",
    "NFL",
    "College football",
    "NBA",
    "F1",
    "Paper Trading",
  ]);
  // The wordmark links home and is the current page on the hub (it sits beside
  // the nav, not inside it, matching GlobalNav's separation of home from the
  // sports row).
  assert.match(html, /<a href="\/" class="wordmark" aria-current="page">Predictor<\/a>/);
  // The active-entry style rule matches GlobalNav's: aria-current entries get
  // the accent fill with stage-coloured ink. Pin it on the same selector the
  // sport sites use (`nav a[aria-current="page"]`) so a future edit can't
  // silently drop the active highlight.
  assert.match(html, /nav\s+a\[aria-current="page"\][\s\S]*?background:\s*var\(--color-pr-accent\)/);
  assert.match(html, /nav\s+a\[aria-current="page"\][\s\S]*?color:\s*var\(--color-pr-accent-ink\)/);
  // Every entry is a real, deep-linkable href (never an in-page button). The
  // sport hosts are asserted by "links point at the VPS sites" above; here we
  // pin Paper Trading's local route and the nav-scroller markup.
  assert.match(html, /<a href="\/paper\.html">Paper Trading<\/a>/);
  assert.match(html, /<div class="nav-scroller">/);
  assert.match(html, /<span class="nav-fade" aria-hidden="true"><\/span>/);
});

test("the landing board: filter chips for every sport, each with its real favicon", () => {
  // The five static sport cards became a filterable board of live cards
  // (home.js). The shell pins what the board needs to exist: one chip per
  // sport in family order, each carrying that sport's shipped favicon —
  // NFL and CFB have distinct icons under the sports host's /favicons/.
  const chips = [...html.matchAll(/<button class="fbtn[^"]*" data-filter="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(chips, ["all", "pl", "nfl", "cfb", "nba", "f1"]);
  for (const sub of ["pl", "nba", "f1"]) assert.match(html, new RegExp(`src="https://${sub}\\.${HOST}/favicon\\.svg"`));
  for (const sport of ["nfl", "cfb"]) assert.match(html, new RegExp(`src="https://sports\\.${HOST}/favicons/${sport}\\.svg"`));
  assert.match(html, /<div id="deckhead"><\/div>/);
  assert.match(html, /<div class="grid" id="grid" aria-busy="true">/);
  assert.match(html, /<div class="kpis" id="kpis" aria-busy="true"><\/div>/);
});

test("inlined tokens match predictor-ui's tokens.css exactly", () => {
  const decls = (css) => Object.fromEntries([...css.matchAll(/(--(?:color|font|radius)-pr-[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  const base = decls(tokens.slice(0, tokens.indexOf("[data-sport=")));
  const mine = decls(html);
  for (const [name, value] of Object.entries(base)) assert.equal(mine[name], value, name);
  for (const sport of ["pl", "f1", "nfl", "cfb", "nba"]) {
    const accent = (css) => css.match(new RegExp(`\\[data-sport="${sport}"\\]\\s*\\{[^}]*--color-pr-accent:\\s*([^;]+);`))?.[1];
    assert.equal(accent(html), accent(tokens), `${sport} accent`);
  }
});

test("the family type and stage: no Inter, no glow, no emoji icons", () => {
  assert.match(html, /family=Barlow/);
  assert.doesNotMatch(html, /Inter/);
  assert.doesNotMatch(html, /radial-gradient/);
  assert.doesNotMatch(html, /\p{Extended_Pictographic}/u);
});

test("honest words only, and nothing on the board under 12px", () => {
  assert.doesNotMatch(html, /Work in progress|walk-forward|no login required/i);
  // The footer states the record framing the decks use: all counted picks
  // headline, a late pick is labelled rather than hidden, and no edge
  // appears without a real line. If the copy drifts, the decks' framing
  // has drifted with it.
  assert.match(html, /Every recorded pick counts, once per game and market, and is never overwritten/);
  assert.match(html, /Made after kickoff/);
  assert.match(html, /No edge is shown without a real line behind it/);
  // The smallest board text (tags, times, footers) is 0.75rem = 12px.
  for (const sel of [".stag", ".ctime", ".cfoot", ".deckhead .big small"]) {
    const size = html.match(new RegExp(`\\${sel.replace(".", ".")}\\s*\\{[^}]*font-size:\\s*([\\d.]+)rem`));
    assert.ok(size && parseFloat(size[1]) >= 0.75, `${sel} text is under 12px`);
  }
});

test("states the honesty rule once, plainly", () => {
  assert.match(html, /Only picks made before the game starts count/);
});

test("calls itself Sports Predictor in the title, the heading, and the share card", () => {
  // The page had three brand surfaces saying "Predictor" and no og:title at
  // all, so a shared link showed the bare family name with no description.
  assert.match(html, /<title>Sports Predictor<\/title>/);
  assert.match(html, /<h1[^>]*>Sports Predictor<\/h1>/);
  const og = html.match(/<meta property="og:title" content="([^"]+)"/);
  assert.ok(og, "no og:title, so a shared link has no title to show");
  assert.equal(og[1], "Sports Predictor");
  // og:description reuses the existing meta description rather than inventing
  // copy: two descriptions would drift, and only one of them would get updated.
  const desc = html.match(/<meta name="description" content="([^"]+)"/);
  const ogDesc = html.match(/<meta property="og:description" content="([^"]+)"/);
  assert.ok(ogDesc, "og:title without og:description is a half-done share card");
  assert.equal(ogDesc[1], desc[1]);
});

test("the wordmark stays the family name, not the product name", () => {
  // Deliberate, and pinned so it cannot drift by accident. The wordmark is the
  // shared frame every Predictor site carries in its header; the five sport
  // sites all say "Predictor" there. This page's own name is Sports Predictor,
  // but making its wordmark differ from the five would break the one thing the
  // wordmark is for -- telling a reader they are in the same family.
  const wordmark = html.match(/class="wordmark"[^>]*>([^<]+)</);
  assert.ok(wordmark, "no wordmark");
  assert.equal(wordmark[1], "Predictor");
});

test("the board is rendered by home.js into the grid, one link per card", () => {
  // Cards are whole-card links painted by home.js (tests/home.test.mjs
  // covers the builders); the shell only carries the containers. teasers.js
  // stays in the repo — home.js imports its pct/driverName/freshness — but
  // the hub page no longer loads it directly or renders its slots.
  assert.doesNotMatch(html, /data-teaser=/);
  assert.doesNotMatch(html, /class="sport pr-notch"/);
  assert.match(html, /<div class="grid" id="grid" aria-busy="true">/);
});

test("the hub loads the home module", () => {
  assert.match(html, /<script type="module" src="\.\/home\.js"><\/script>/);
});

