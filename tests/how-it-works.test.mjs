import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const html = read("../how-it-works.html");
const tokens = read("../packages/predictor-ui/src/tokens.css");

test("carries the hub nav with How it works as the current entry", () => {
  const nav = html.match(/<nav aria-label="Sports"[\s\S]*?<\/nav>/)[0];
  assert.deepEqual([...nav.matchAll(/>(\w+[\w ]*)<\/a>/g)].map((m) => m[1].trim()), [
    "Home", "Premier League", "NFL", "College football", "NBA", "F1", "Paper Trading", "How it works",
  ]);
  assert.match(nav, /<a href="\/how-it-works\.html" aria-current="page">How it works<\/a>/);
  for (const page of ["../index.html", "../paper.html"]) assert.match(read(page), /<a href="\/how-it-works\.html">How it works<\/a>/);
});

test("a merge that changes the page deploys it", () => {
  assert.match(read("../.github/workflows/deploy-hub.yml"), /- 'how-it-works\.html'/);
});

test("inlined tokens match predictor-ui's tokens.css", () => {
  const decls = (css) => Object.fromEntries([...css.matchAll(/(--(?:color|font|radius)-pr-[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  const mine = decls(html);
  for (const [name, value] of Object.entries(decls(tokens.slice(0, tokens.indexOf("[data-sport="))))) assert.equal(mine[name], value, name);
});

test("reads the live paper verdicts, not a baked-in copy of them", () => {
  assert.match(html, /raw\.githubusercontent\.com\/Kevocado\/predictor-hub\/main\/data\/paper\/report\.json/);
  assert.doesNotMatch(html, /"status":\s*"(proven|leaning|against|not_yet)"/);
});
