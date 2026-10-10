import test from "node:test";
import assert from "node:assert/strict";
import { executeMonitoringBatch, executeRecordedMonitoringBatch } from "./monitoringBatch.js";

test("external batch operation stops after an uncertain result", async () => {
  const called = [];
  const progress = [];
  const result = await executeMonitoringBatch([
    { conversation_id: "1" }, { conversation_id: "2" }, { conversation_id: "3" },
  ], {
    stopOnFailure: true,
    async perform(item) {
      called.push(item.conversation_id);
      if (item.conversation_id === "2") throw new Error("结果不明");
    },
  }, (done, total) => progress.push(`${done}/${total}`));
  assert.deepEqual(called, ["1", "2"]);
  assert.deepEqual(progress, ["1/3", "2/3"]);
  assert.equal(result.success, 1);
  assert.equal(result.processed, 2);
  assert.match(result.failures[0], /结果不明/);
});

test("local record deletion continues and reports individual failures", async () => {
  const called = [];
  const result = await executeMonitoringBatch([
    { conversation_id: "1" }, { conversation_id: "2" }, { conversation_id: "3" },
  ], {
    stopOnFailure: false,
    async perform(item) {
      called.push(item.conversation_id);
      if (item.conversation_id === "2") throw new Error("记录不存在");
    },
  }, () => {});
  assert.deepEqual(called, ["1", "2", "3"]);
  assert.equal(result.success, 2);
  assert.equal(result.processed, 3);
  assert.equal(result.failures.length, 1);
});

test("recorded followups submit once and poll the persisted run until it finishes", async () => {
  const updates = [];
  const calls = [];
  const states = [
    { run_id: 42, status: "running", counts: { total: 2, completed: 1 } },
    { run_id: 42, status: "completed", counts: { total: 2, completed: 2 } },
  ];
  const result = await executeRecordedMonitoringBatch([
    { conversation_id: "1", text: "草稿一" }, { conversation_id: "2", text: "草稿二" },
  ], {
    batchAction: "followup",
    toBatchItem: (item) => ({ platform: "boss", ...item }),
    perform: () => assert.fail("recorded batches must execute on the server"),
  }, (state) => updates.push(state.counts.completed), {
    async start(action, items) {
      calls.push({ action, items });
      return { run_id: 42, status: "running", counts: { total: 2, completed: 0 } };
    },
    async read(runId) { assert.equal(runId, 42); return states.shift(); },
    async wait() {},
  });
  assert.deepEqual(calls, [{ action: "followup", items: [
    { platform: "boss", conversation_id: "1", text: "草稿一" },
    { platform: "boss", conversation_id: "2", text: "草稿二" },
  ] }]);
  assert.deepEqual(updates, [0, 1, 2]);
  assert.equal(result.status, "completed");
});

test("recorded deletion retains actual completed count on a partial result", async () => {
  const terminal = { run_id: 7, status: "completed_with_shortage", counts: {
    total: 3, completed: 1, failed: 1, skipped: 1,
  } };
  const result = await executeRecordedMonitoringBatch([{ conversation_id: "1" }], {
    batchAction: "delete", toBatchItem: (item) => item,
  }, () => {}, {
    async start() { return { run_id: 7, status: "running" }; },
    async read() { return terminal; }, async wait() {},
  });
  assert.deepEqual(result, terminal);
});

test("polling a different run never replaces the submitted batch", async () => {
  await assert.rejects(executeRecordedMonitoringBatch([], {
    batchAction: "delete", toBatchItem: (item) => item,
  }, () => {}, {
    async start() { return { run_id: 7, status: "running" }; },
    async read() { return { run_id: 8, status: "completed" }; }, async wait() {},
  }), /编号已变化/);
});
