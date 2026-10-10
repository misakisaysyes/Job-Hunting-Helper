import test from "node:test";
import assert from "node:assert/strict";
import { canGenerateFollowup, createFollowupGenerationAction } from "./followupGeneration.js";
import { executeMonitoringBatch } from "./monitoringBatch.js";

const conversation = {
  platform: "boss", conversation_id: "1", followup_status: "pending_review",
  followup_eligible: true, followup_ai_available: true, followup_cooldown_until: "",
  followup_anchor_id: "100", saved_followup_text: "已保存的原招呼语",
  followup_text: "尚未保存的编辑",
};

test("generation skips cooling, missing JD, and conversations outside the editable state", () => {
  assert.equal(canGenerateFollowup(conversation), true);
  for (const changes of [
    { followup_eligible: false }, { followup_ai_available: false },
    { followup_cooldown_until: "2026-10-11T12:00:00+08:00" },
    { followup_status: "sending" }, { followup_status: "idle" },
    { followup_status: "unknown" },
  ]) {
    assert.equal(canGenerateFollowup({ ...conversation, ...changes }), false);
  }
  assert.equal(canGenerateFollowup({ ...conversation, followup_text: "" }), true);
});

test("parallel generation preserves saved-draft evidence and reports failures without losing successes", async () => {
  const items = ["1", "2", "3"].map((id) => ({ ...conversation, conversation_id: id }));
  const calls = [];
  const updates = [];
  const progress = [];
  let releaseFirst;
  const gate = new Promise((resolve) => { releaseFirst = resolve; });
  const action = createFollowupGenerationAction(async (platform, id, options) => {
    calls.push({ platform, id, options });
    if (id === "1") await gate;
    if (id === "3") throw new Error("会话或追问草稿已变化");
    return { conversation: { ...items.find((item) => item.conversation_id === id),
      followup_text: `已保存的 AI 追问语 ${id}` } };
  }, (item) => updates.push(item));
  const pending = executeMonitoringBatch(items, action, (done, total) => progress.push([done, total]));
  assert.deepEqual(calls.map((call) => call.id), ["1", "2", "3"]);
  assert.ok(calls.every((call) => call.options.anchor_id === "100"
    && call.options.expected_text === "已保存的原招呼语"));
  releaseFirst();
  const result = await pending;
  assert.deepEqual(updates.map((item) => item.conversation_id).sort(), ["1", "2"]);
  assert.ok(updates.every((item) => item.followup_text.startsWith("已保存的 AI 追问语")));
  assert.deepEqual(progress, [[1, 3], [2, 3], [3, 3]]);
  assert.equal(result.success, 2);
  assert.equal(result.processed, 3);
  assert.equal(result.failures.length, 1);
  assert.match(result.failures[0], /3：会话或追问草稿已变化/);
});
