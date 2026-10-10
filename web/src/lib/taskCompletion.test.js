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

test("batch followup and deletion completions each alert once", () => {
  const before = currentTaskRuns({ current: { monitoring_followup: run(4, "running") } });
  const after = currentTaskRuns({ current: { monitoring_followup: run(4, "completed") } });
  assert.equal(hasNewTaskCompletion(before, after), true);
  assert.equal(hasNewTaskCompletion(after, after), false);
  const deleted = currentTaskRuns({ current: {
    monitoring_followup: run(4, "completed"), monitoring_delete: run(5, "completed_with_shortage"),
  } });
  assert.equal(hasNewTaskCompletion(after, deleted), true);
  assert.equal(hasNewTaskCompletion(deleted, deleted), false);
});
