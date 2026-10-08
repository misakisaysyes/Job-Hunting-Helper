import test from "node:test";
import assert from "node:assert/strict";
import { currentTaskRuns, hasNewTaskCompletion } from "./taskCompletion.js";

const run = (run_id, status) => ({ run_id, status });

test("ignores historical completions on initial load", () => {
  const current = currentTaskRuns({ current: {
    collection: run(1, "completed"), monitoring: run(2, "idle"),
  } });
  assert.equal(hasNewTaskCompletion(null, current), false);
  assert.equal(hasNewTaskCompletion(current, current), false);
});

test("alerts once when either task finishes", () => {
  const before = { collection: run(1, "running"), monitoring: run(2, "running") };
  const collectionDone = { ...before, collection: run(1, "completed_with_shortage") };
  const bothDone = { collection: collectionDone.collection, monitoring: run(2, "completed") };
  assert.equal(hasNewTaskCompletion(before, collectionDone), true);
  assert.equal(hasNewTaskCompletion(collectionDone, bothDone), true);
  assert.equal(hasNewTaskCompletion(bothDone, bothDone), false);
});

test("detects a new task that starts and finishes between polls", () => {
  const before = { collection: run(1, "completed"), monitoring: null };
  assert.equal(hasNewTaskCompletion(before, {
    collection: run(2, "failed"), monitoring: null,
  }), true);
});
