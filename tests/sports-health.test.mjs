// The health checker is the thing that tells Kevin a scheduled refresh died.
// These tests inject the clock and fetch, so no network and no real time leaks in.
import test from "node:test";
import assert from "node:assert/strict";

import {
  CHECKS,
  checkWorkflows,
  formatMessage,
  main,
  sendTelegram,
  splitMessage,
  TELEGRAM_MAX_CHARS,
} from "../scripts/sports-health.mjs";

const NOW = new Date("2026-11-23T12:00:00Z");
const TOKEN = "super-secret-bot-token";
const CHAT = "123456";

const telegramConfig = { TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_CHAT_ID: CHAT };
const hoursAgo = (hours) => new Date(NOW.getTime() - hours * 3600_000).toISOString();

function run({ status = "completed", conclusion = "success", ageHours = 1, url } = {}) {
  return {
    status,
    conclusion,
    updated_at: hoursAgo(ageHours),
    html_url: url ?? "https://github.com/kevinsigey/NBA_Predictor/actions/runs/1",
  };
}

// Every check is healthy unless a test overrides one by repo/workflow name.
function makeFetch({ overrides = {}, telegramStatus = 200 } = {}) {
  const githubCalls = [];
  const telegramCalls = [];
  const fetch = async (url, options) => {
    if (url.startsWith("https://api.telegram.org/")) {
      telegramCalls.push({ url, options });
      return { ok: telegramStatus === 200, status: telegramStatus };
    }
    githubCalls.push({ url, options });
    const check = CHECKS.find((c) => url.includes(`/${c.repo}/actions/workflows/${c.workflow}/runs`));
    assert.ok(check, `unexpected fetch: ${url}`);
    const key = `${check.repo}/${check.workflow}`;
    const override = overrides[key];
    if (override?.error) return { ok: false, status: override.error };
    return {
      ok: true,
      status: 200,
      json: async () => ({ workflow_runs: override?.runs ?? [run()] }),
    };
  };
  return { fetch, githubCalls, telegramCalls };
}

test("the table is the listed scheduled jobs, with a floor on every max age", () => {
  const keys = CHECKS.map((c) => `${c.repo}/${c.workflow}`);
  assert.equal(keys.length, new Set(keys).size, "duplicate check in the table");
  assert.deepEqual(keys.sort(), [
    "CFB_Predictor/collect-kalshi-lines.yml",
    "CFB_Predictor/refresh-public-snapshot.yml",
    "F1_Predictor/collect-kalshi-lines.yml",
    "F1_Predictor/refresh-public-snapshot.yml",
    "F1_Predictor/retrain-after-race.yml",
    "NBA_Predictor/collect-injuries.yml",
    "NBA_Predictor/collect-kalshi-lines.yml",
    "NBA_Predictor/refresh-data.yml",
    "NBA_Predictor/refresh-public-snapshot.yml",
    "NFL_Predictor/collect-kalshi-lines.yml",
    "NFL_Predictor/refresh-public-snapshot.yml",
    "PL_Predictor/collect-kalshi-lines.yml",
    "PL_Predictor/refresh-public-snapshot.yml",
    "predictor-hub/paper-log.yml",
    "predictor-hub/paper-settle.yml",
  ]);
  for (const check of CHECKS) {
    if (check.maxAgeHours === null) continue; // the race retrain has no clock
    assert.ok(Number.isInteger(check.maxAgeHours) && check.maxAgeHours >= 6,
      `${check.repo}/${check.workflow} has no sensible max age`);
  }
  // The race retrain only runs twice a week; it is a failure check, not a clock.
  assert.equal(CHECKS.find((c) => c.workflow === "retrain-after-race.yml").maxAgeHours, null);
});

test("an in-progress run above a failed completed run still reports failed", async () => {
  const url = "https://github.com/kevinsigey/NBA_Predictor/actions/runs/42";
  const { fetch } = makeFetch({
    overrides: {
      "NBA_Predictor/collect-injuries.yml": {
        runs: [run({ status: "in_progress", conclusion: null, ageHours: 0 }),
          run({ conclusion: "failure", ageHours: 2, url })],
      },
    },
  });
  const problems = await checkWorkflows({ fetch, now: NOW, env: {} });
  assert.deepEqual(problems.map((p) => p.kind), ["failed"]);
  assert.ok(problems[0].line.includes("NBA_Predictor/collect-injuries.yml"));
  assert.match(problems[0].line, /failed/);
  assert.match(problems[0].line, /collect-injuries\.yml/);
  assert.ok(problems[0].line.includes(url), "the problem line must carry the run URL");
});

test("failure, timed_out and startup_failure all count as failed", async () => {
  for (const conclusion of ["failure", "timed_out", "startup_failure"]) {
    const { fetch } = makeFetch({
      overrides: {
        "NFL_Predictor/collect-kalshi-lines.yml": {
          runs: [run({ conclusion, ageHours: 1 })],
        },
      },
    });
    const problems = await checkWorkflows({ fetch, now: NOW, env: {} });
    assert.equal(problems.length, 1, `${conclusion} was not reported`);
    assert.equal(problems[0].kind, "failed");
  }
});

test("a success older than the max age reports did not run", async () => {
  const url = "https://github.com/kevinsigey/CFB_Predictor/actions/runs/99";
  const check = CHECKS.find((c) => c.repo === "CFB_Predictor" && c.workflow === "refresh-public-snapshot.yml");
  const { fetch } = makeFetch({
    overrides: {
      [`${check.repo}/${check.workflow}`]: {
        runs: [run({ ageHours: check.maxAgeHours + 2, url })],
      },
    },
  });
  const problems = await checkWorkflows({ fetch, now: NOW, env: {} });
  assert.deepEqual(problems.map((p) => p.kind), ["stale"]);
  assert.match(problems[0].line, /did not run/);
  assert.ok(problems[0].line.includes(url));
});

test("a success at the edge of the max age is still ok", async () => {
  const check = CHECKS.find((c) => c.workflow === "paper-log.yml");
  const { fetch } = makeFetch({
    overrides: {
      [`${check.repo}/${check.workflow}`]: {
        runs: [run({ ageHours: check.maxAgeHours - 1 })],
      },
    },
  });
  assert.deepEqual(await checkWorkflows({ fetch, now: NOW, env: {} }), []);
});

test("a healthy suite sends nothing and prints all checks ok", async () => {
  const { fetch, telegramCalls } = makeFetch();
  const lines = [];
  const code = await main({
    fetch, now: NOW, env: telegramConfig, log: (line) => lines.push(line),
  });
  assert.equal(code, 0);
  assert.deepEqual(lines, [`all ${CHECKS.length} checks ok`]);
  assert.equal(telegramCalls.length, 0, "a healthy report must never ping Kevin");
});

test("an API error is its own problem, does not stop the other checks, and exits non-zero", async () => {
  const url = "https://github.com/kevinsigey/PL_Predictor/actions/runs/7";
  const { fetch, githubCalls } = makeFetch({
    overrides: {
      "PL_Predictor/collect-kalshi-lines.yml": { error: 500 },
      "F1_Predictor/collect-kalshi-lines.yml": { runs: [run({ conclusion: "failure", ageHours: 1, url })] },
    },
  });
  const lines = [];
  const code = await main({ fetch, now: NOW, env: {}, log: (line) => lines.push(line) });
  assert.equal(code, 1, "a check that could not run at all must fail the job");
  assert.equal(githubCalls.length, CHECKS.length, "one error must not abort the remaining rows");
  const problems = lines.filter((l) => l.startsWith("- "));
  assert.equal(problems.length, 2);
  assert.ok(problems.some((l) => l.includes("could not check") && l.includes("collect-kalshi-lines.yml")));
  assert.ok(problems.some((l) => l.includes("failed") && l.includes(url)));
});

test("without telegram config the problems are printed, nothing is sent, and exit stays 0", async () => {
  const { fetch, telegramCalls } = makeFetch({
    overrides: { "NBA_Predictor/refresh-data.yml": { runs: [run({ conclusion: "failure" })] } },
  });
  const lines = [];
  const code = await main({
    fetch, now: NOW, env: { GITHUB_TOKEN: TOKEN }, log: (line) => lines.push(line),
  });
  assert.equal(code, 0);
  assert.equal(telegramCalls.length, 0);
  const text = lines.join("\n");
  assert.match(text, /telegram not configured/);
  assert.match(text, /refresh-data\.yml/);
  assert.ok(!text.includes(TOKEN), "the token must never be printed");
  // One env var alone is not configuration.
  const halfConfigured = await main({
    fetch, now: NOW, env: { TELEGRAM_CHAT_ID: CHAT }, log: () => {},
  });
  assert.equal(halfConfigured, 0);
});

test("one telegram message carries all problems with the configured chat id", async () => {
  const { fetch, telegramCalls } = makeFetch({
    overrides: {
      "NBA_Predictor/refresh-data.yml": { runs: [run({ conclusion: "failure" })] },
      "predictor-hub/paper-settle.yml": { runs: [run({ conclusion: "timed_out" })] },
    },
  });
  const code = await main({ fetch, now: NOW, env: telegramConfig, log: () => {} });
  assert.equal(code, 0);
  assert.equal(telegramCalls.length, 1, "one logical message, not one per problem");
  const [call] = telegramCalls;
  assert.match(call.url, /api\.telegram\.org\/bot/);
  assert.ok(call.url.includes(TOKEN));
  assert.ok(!call.url.includes(CHAT), "the chat id belongs in the body");
  const body = JSON.parse(call.options.body);
  assert.equal(body.chat_id, CHAT);
  assert.match(body.text, /refresh-data\.yml/);
  assert.match(body.text, /problem\(s\)/);
});

test("a long message is split on line boundaries and no line is lost", async () => {
  const problems = Array.from({ length: 60 }, (_, i) => ({
    kind: "failed",
    line: `- predictor-hub/paper-log.yml: failed ${i} ${"x".repeat(120)} https://github.com/kevinsigey/predictor-hub/actions/runs/${i}`,
  }));
  const message = formatMessage(problems, NOW);
  const chunks = splitMessage(message);
  assert.ok(chunks.length > 1, "a 60-line alert must not go out as one rejected message");
  for (const chunk of chunks) assert.ok(chunk.length <= TELEGRAM_MAX_CHARS);
  const rejoined = chunks.join("\n");
  for (const p of problems) assert.ok(rejoined.includes(p.line), `split dropped ${p.line.slice(0, 40)}`);
  assert.deepEqual(splitMessage("short"), ["short"]);
  // A single line longer than the limit is hard-split rather than dropped.
  const huge = "y".repeat(TELEGRAM_MAX_CHARS + 10);
  assert.deepEqual(splitMessage(huge), [huge.slice(0, TELEGRAM_MAX_CHARS), huge.slice(TELEGRAM_MAX_CHARS)]);

  const { fetch, telegramCalls } = makeFetch();
  const sent = await sendTelegram(message, { fetch, env: telegramConfig });
  assert.equal(sent, true);
  assert.equal(telegramCalls.length, chunks.length);
  telegramCalls.forEach((call, i) => {
    assert.equal(JSON.parse(call.options.body).text, chunks[i]);
  });
});

test("a rejected telegram post does not fail the job", async () => {
  const { fetch, telegramCalls } = makeFetch({ telegramStatus: 500 });
  const lines = [];
  const code = await main({
    fetch, now: NOW, env: telegramConfig, log: (line) => lines.push(line),
  });
  // main's own rows are all healthy in this fake, so drive the failing send
  // path directly and check it reports false rather than throwing.
  assert.equal(code, 0);
  assert.equal(lines.length, 1);
  assert.equal(await sendTelegram("hello", { fetch, env: telegramConfig }), false);
  assert.equal(telegramCalls.length, 1);
});
