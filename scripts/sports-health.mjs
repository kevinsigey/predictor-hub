// One Telegram message when a scheduled sports refresh is broken, nothing when it is not.
// Ported from the trade hub's health alerts: never print a token, and an unconfigured
// notifier is a no-op that says so rather than an error.
//
// Plain Node, no dependencies. Fetch and the clock are injected so the tests can
// drive it without a network. This file runs from cron every six hours.
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const OWNER = "kevinsigey";
const GITHUB_API = "https://api.github.com";
const TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage";
export const TELEGRAM_MAX_CHARS = 4096;
const BAD_CONCLUSIONS = new Set(["failure", "timed_out", "startup_failure"]);

// One row per workflow that matters. GitHub delays scheduled runs by hours and
// skips some slots outright (on 2026-11-23 the 14:10 Kalshi collectors ran at
// ~20:40 or not at all; NFL's every-3h snapshot landed every 6-11h), so max ages
// follow the OBSERVED cadence, not the cron: 18h for jobs that fire every few
// hours, 26h for twice-daily jobs, 36h for daily jobs. The per-row comments
// below give each cron as written.
export const CHECKS = [
  // 30 12 / 30 17 / 45 21 / 30 1 UTC: 11h overnight gap, so 12h.
  { repo: "NBA_Predictor", workflow: "collect-injuries.yml", maxAgeHours: 26 },
  // 30 15 and 15 23 UTC: ~8h apart.
  { repo: "NBA_Predictor", workflow: "collect-kalshi-lines.yml", maxAgeHours: 26 },
  // Daily at 12:00 UTC: one missed day plus the six-hour check grace.
  { repo: "NBA_Predictor", workflow: "refresh-data.yml", maxAgeHours: 36 },
  // Every 6h (0 */6): two missed slots.
  { repo: "NBA_Predictor", workflow: "refresh-public-snapshot.yml", maxAgeHours: 18 },
  // 10 14 and 40 22 UTC: ~8h apart.
  { repo: "NFL_Predictor", workflow: "collect-kalshi-lines.yml", maxAgeHours: 26 },
  // Every 3h (20 */3), so two missed slots is six hours.
  { repo: "NFL_Predictor", workflow: "refresh-public-snapshot.yml", maxAgeHours: 18 },
  // 10 14 and 40 01 UTC: ~11h apart.
  { repo: "CFB_Predictor", workflow: "collect-kalshi-lines.yml", maxAgeHours: 26 },
  // Every 4h (25 */4), so two missed slots is eight hours.
  { repo: "CFB_Predictor", workflow: "refresh-public-snapshot.yml", maxAgeHours: 18 },
  // 10 14 and 40 22 UTC: ~8h apart.
  { repo: "PL_Predictor", workflow: "collect-kalshi-lines.yml", maxAgeHours: 26 },
  // Every 20 min on matchdays, daily at 08:00 on quiet days: one missed day plus grace.
  { repo: "PL_Predictor", workflow: "refresh-public-snapshot.yml", maxAgeHours: 36 },
  // 10 14 and 40 22 UTC: ~8h apart.
  { repo: "F1_Predictor", workflow: "collect-kalshi-lines.yml", maxAgeHours: 26 },
  // Every 6h (40 */6), with 20-minute race-weekend runs on top.
  { repo: "F1_Predictor", workflow: "refresh-public-snapshot.yml", maxAgeHours: 18 },
  // 30 8 on Mon/Tue only: twice a week, so there is no useful max age. Failure only.
  { repo: "F1_Predictor", workflow: "retrain-after-race.yml", maxAgeHours: null },
  // 45 15 and 35 23 UTC: ~8h apart.
  { repo: "predictor-hub", workflow: "paper-log.yml", maxAgeHours: 26 },
  // 20 4 / 20 12 / 20 20 UTC: every 8h.
  { repo: "predictor-hub", workflow: "paper-settle.yml", maxAgeHours: 26 },
];

const setting = (env, name) => (env?.[name] || "").trim();
const telegramConfigured = (env) => Boolean(setting(env, "TELEGRAM_BOT_TOKEN") && setting(env, "TELEGRAM_CHAT_ID"));

// One line per broken check, with the URL a human can click. `kind: error` is
// the only one that fails the job: it means we genuinely did not check it.
export async function checkWorkflows({ fetch: http = fetch, now = new Date(), env = process.env } = {}) {
  const headers = { Accept: "application/vnd.github+json", "User-Agent": "sports-health" };
  const githubToken = setting(env, "GITHUB_TOKEN");
  if (githubToken) headers.Authorization = `Bearer ${githubToken}`;

  const problems = [];
  for (const check of CHECKS) {
    const prefix = `${check.repo}/${check.workflow}:`;
    const workflowUrl = `https://github.com/${OWNER}/${check.repo}/actions/workflows/${check.workflow}`;
    try {
      const response = await http(
        `${GITHUB_API}/repos/${OWNER}/${check.repo}/actions/workflows/${check.workflow}/runs?per_page=5`,
        { headers }
      );
      if (!response.ok) throw new Error(`GitHub returned ${response.status}`);
      const data = await response.json();
      const runs = Array.isArray(data?.workflow_runs) ? data.workflow_runs : [];
      // Queued and in-progress runs are not conclusions. Only a completed run
      // can tell us whether the job is broken.
      const completed = runs.filter((r) => r.status === "completed");
      const latest = completed[0];
      if (latest && BAD_CONCLUSIONS.has(latest.conclusion)) {
        problems.push({
          kind: "failed",
          line: `${prefix} failed (${latest.conclusion}) ${latest.html_url || workflowUrl}`,
        });
        continue;
      }
      if (check.maxAgeHours === null) continue; // failure check only
      const success = completed.find((r) => r.conclusion === "success");
      if (!success) {
        problems.push({
          kind: "stale",
          line: `${prefix} did not run (no successful run in the last 5) ${latest?.html_url || workflowUrl}`,
        });
        continue;
      }
      const lastSuccess = Date.parse(success.updated_at || success.created_at);
      const ageHours = (now.getTime() - lastSuccess) / 3600_000;
      if (!(ageHours < check.maxAgeHours)) {
        problems.push({
          kind: "stale",
          line: `${prefix} did not run (last success ${ageHours.toFixed(1)}h ago, max ${check.maxAgeHours}h) ${success.html_url || workflowUrl}`,
        });
      }
    } catch (error) {
      // An API problem is a problem line, never a silently skipped row.
      problems.push({
        kind: "error",
        line: `${prefix} could not check (${error.message}) ${workflowUrl}`,
      });
    }
  }
  return problems;
}

// Empty for a healthy report: that is what keeps the notifier from sending a
// "nothing is wrong" ping every six hours.
export function formatMessage(problems, now = new Date()) {
  if (!problems.length) return "";
  const lines = ["Sports predictor health check", `as of ${now.toISOString()}`, ""];
  for (const problem of problems) lines.push(`- ${problem.line}`);
  lines.push("", `${problems.length} problem(s). Please check.`);
  return lines.join("\n");
}

// Telegram rejects a message over 4096 chars outright, so split it rather than
// lose the alert. Split on line boundaries; hard-split a single overlong line.
export function splitMessage(message, limit = TELEGRAM_MAX_CHARS) {
  if (message.length <= limit) return [message];
  const chunks = [];
  let current = "";
  for (let line of message.split("\n")) {
    while (line.length > limit) {
      if (current) { chunks.push(current); current = ""; }
      chunks.push(line.slice(0, limit));
      line = line.slice(limit);
    }
    const candidate = current ? `${current}\n${line}` : line;
    if (candidate.length > limit) {
      if (current) chunks.push(current);
      current = line;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

export async function sendTelegram(message, { fetch: http = fetch, env = process.env } = {}) {
  const token = setting(env, "TELEGRAM_BOT_TOKEN");
  const chatId = setting(env, "TELEGRAM_CHAT_ID");
  if (!message || !token || !chatId) return false;
  try {
    for (const chunk of splitMessage(message)) {
      const response = await http(TELEGRAM_URL.replace("{token}", token), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text: chunk }),
      });
      if (!response.ok) return false;
    }
    return true;
  } catch {
    return false; // a notifier must never crash the health check
  }
}

export async function main({ fetch: http = fetch, now = new Date(), env = process.env, log = console.log } = {}) {
  const problems = await checkWorkflows({ fetch: http, now, env });
  const failedChecks = problems.some((p) => p.kind === "error") ? 1 : 0;
  if (!problems.length) {
    log(`all ${CHECKS.length} checks ok`);
    return failedChecks;
  }
  if (!telegramConfigured(env)) {
    for (const problem of problems) log(`- ${problem.line}`);
    log("telegram not configured");
    return failedChecks;
  }
  const sent = await sendTelegram(formatMessage(problems, now), { fetch: http, env });
  log(sent ? `sent ${problems.length} problem(s) to telegram` : "telegram send failed");
  return sent ? failedChecks : 1; // a lost alert must show as a failed job
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
