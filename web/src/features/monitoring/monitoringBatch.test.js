import test from "node:test";
import assert from "node:assert/strict";
import { executeMonitoringBatch } from "./monitoringBatch.js";

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
