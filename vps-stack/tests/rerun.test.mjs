/**
 * The Health re-run watcher (trade-hub phase-2 section 4.3).
 *
 * The failure this pins is a double fire: the watcher ticks every minute and a scan
 * takes longer than that, so a trigger that is still sitting in the directory on the
 * next tick would start the job again. The watcher therefore CLAIMS the trigger by
 * moving it into done/ before running anything, and defers while the scheduled unit
 * is already running the job. Both orderings are asserted on the script's text: this
 * repo's tests read wiring, they do not execute host scripts.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const stack = join(here, "..");
const watcher = readFileSync(join(stack, "bin", "tradehub-rerun"), "utf8");
const service = readFileSync(join(stack, "systemd", "tradehub-rerun.service"), "utf8");
const timer = readFileSync(join(stack, "systemd", "tradehub-rerun.timer"), "utf8");

test("the watcher runs every minute as deploy, through the timer", () => {
  assert.match(timer, /OnUnitActiveSec=1min/);
  assert.match(service, /User=deploy/);
  assert.match(service, /ExecStart=\/opt\/stack\/bin\/tradehub-rerun/);
});

test("the watcher claims a trigger into done/ before it runs the job", () => {
  const claim = watcher.indexOf('mv "$trigger" "$claimed"');
  const run = watcher.indexOf(String.raw`"$STACK_DIR/bin/tradehub-job" "$job"`);
  assert.ok(claim > -1 && run > -1 && claim < run, "claim (mv) must precede the job run");
  assert.match(watcher, /DONE_DIR="\$RERUN_DIR\/done"/);
});

test("the watcher defers while the scheduled unit is already running the job", () => {
  assert.match(watcher, /systemctl is-active --quiet "tradehub-\$job\.service"/);
});

test("only scan and settle are claimable, from the shared cache volume", () => {
  assert.match(watcher, /for job in scan settle; do/);
  assert.match(watcher, /volumes\/tradehub-cache\/rerun/);
});
