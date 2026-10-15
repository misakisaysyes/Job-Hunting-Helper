import test from "node:test";
import assert from "node:assert/strict";
import { claimTaskCompletion, createTaskCompletionTracker } from "./taskCompletion.js";

const run = (run_id, status) => ({ run_id, status });

test("ignores completed runs found on initial load", () => {
  const tracker = createTaskCompletionTracker();
  assert.equal(tracker.observe(run(1, "completed")), false);
  assert.equal(tracker.observe(run(1, "completed")), false);
});

test("alerts exactly once when an observed run finishes", () => {
  const tracker = createTaskCompletionTracker();
  assert.equal(tracker.observe(run(1, "running")), false);
  assert.equal(tracker.observe(run(1, "running")), false);
  assert.equal(tracker.observe(run(1, "completed")), true);
  assert.equal(tracker.observe(run(1, "completed")), false);
  assert.equal(tracker.observe(run(1, "running")), false);
  assert.equal(tracker.observe(run(1, "completed")), false);
});

test("alerts once per new run, including failed and interrupted runs", () => {
  const tracker = createTaskCompletionTracker();
  assert.equal(tracker.observe(run(1, "running")), false);
  assert.equal(tracker.observe(run(1, "failed")), true);
  assert.equal(tracker.observe(run(2, "running")), false);
  assert.equal(tracker.observe(run(2, "stopped")), true);
  assert.equal(tracker.observe(run(2, "stopped")), false);
});

test("ignores missing IDs and stale completion responses", () => {
  const tracker = createTaskCompletionTracker();
  assert.equal(tracker.observe(run(1, "running")), false);
  assert.equal(tracker.observe(run(2, "running")), false);
  assert.equal(tracker.observe(run(1, "completed")), false);
  assert.equal(tracker.observe({ status: "completed" }), false);
  assert.equal(tracker.observe(run(2, "completed")), true);
});

test("only one tab claims the sound for the same completed run", async () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
  };
  const firstTab = createTaskCompletionTracker();
  const secondTab = createTaskCompletionTracker();
  firstTab.observe(run(3, "running"));
  secondTab.observe(run(3, "running"));
  assert.equal(firstTab.observe(run(3, "completed")), true);
  assert.equal(secondTab.observe(run(3, "completed")), true);
  assert.equal(await claimTaskCompletion("monitoring:3", { storage, locks: null }), true);
  assert.equal(await claimTaskCompletion("monitoring:3", { storage, locks: null }), false);
  assert.equal(await claimTaskCompletion("monitoring:4", { storage, locks: null }), true);
});
