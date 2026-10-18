import test from "node:test";
import assert from "node:assert/strict";
import { canSubmitFollowup, createFollowupSendingAction } from "./followupSending.js";
import { selectPageConversations } from "./monitoringSelection.js";
import { executeRecordedMonitoringBatch } from "./monitoringBatch.js";

const prepared = {
  platform: "boss", conversation_id: "1", followup_status: "pending_review",
  followup_display_status: "available", followup_eligible: true,
  followup_anchor_id: "100", saved_followup_text: "已保存的追问语",
  followup_text: "当前编辑的追问语",
};

test("available conversations without prepared drafts never enter a sending batch", () => {
  assert.equal(canSubmitFollowup(prepared), true);
  assert.equal(canSubmitFollowup({ ...prepared, followup_status: "none",
    followup_eligible: false, followup_anchor_id: "", saved_followup_text: "",
    followup_text: "详情中回退显示的原招呼语" }), false);
  for (const changes of [
    { followup_status: "idle" }, { followup_status: "sending" },
    { followup_display_status: "unavailable" }, { followup_anchor_id: "" },
    { followup_anchor_id: "100\n" }, { followup_anchor_id: 100 },
    { saved_followup_text: undefined }, { followup_text: "字".repeat(301) },
  ]) {
    assert.equal(canSubmitFollowup({ ...prepared, ...changes }), false);
  }
});

test("empty edits retain per-row failure handling instead of aborting the whole batch", () => {
  const empty = { ...prepared, followup_text: "  ", followup_anchor_id: "" };
  const action = createFollowupSendingAction(() => {});
  assert.equal(action.eligible(empty), true);
  assert.deepEqual(action.toBatchItem(empty), {
    platform: "boss", conversation_id: "1", text: "  ", anchor_id: "",
    expected_text: "已保存的追问语",
  });
});

test("52 cross-page selections submit only the 49 prepared drafts with their evidence", async () => {
  const records = Array.from({ length: 52 }, (_, index) => ({
    ...prepared, conversation_id: String(index + 1),
    ...(index < 49 ? {} : { followup_status: "none", followup_eligible: false,
      followup_anchor_id: "", saved_followup_text: "", followup_text: "原招呼语" }),
  }));
  let selected = new Map();
  for (let offset = 0; offset < records.length; offset += 15) {
    selected = selectPageConversations(selected, records.slice(offset, offset + 15), true);
  }
  assert.equal(selected.size, 52);
  const action = createFollowupSendingAction(() => assert.fail("send on the server only"));
  const eligible = [...selected.values()].filter(action.eligible);
  assert.equal(selected.size - eligible.length, 3);
  let submitted;
  await executeRecordedMonitoringBatch(eligible, action, () => {}, {
    async start(name, items) {
      assert.equal(name, "followup");
      submitted = items;
      return { run_id: 1, status: "completed", counts: { total: items.length } };
    },
    read: () => assert.fail("the completed batch needs no polling"),
    wait: () => assert.fail("the completed batch needs no waiting"),
  });
  assert.equal(submitted.length, 49);
  assert.deepEqual(submitted.map((item) => item.conversation_id),
    Array.from({ length: 49 }, (_, index) => String(index + 1)));
  assert.ok(submitted.every((item) => item.anchor_id === "100"
    && item.expected_text === "已保存的追问语" && item.text === "当前编辑的追问语"));
});
