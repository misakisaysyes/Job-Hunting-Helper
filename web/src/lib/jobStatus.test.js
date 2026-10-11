import test from "node:test";
import assert from "node:assert/strict";
import { canGenerateGreeting, canOverrideGreetingThreshold, canStartGreeting, greetingSendIssue } from "./jobStatus.js";

const job = {
  job_status: "scored", greeting_send_state: "idle",
  ai_score_status: "scored", ai_score: 71,
};

test("batch greeting transitions include qualifying scores and skip low or failed scores", () => {
  const jobs = [
    { ...job, id: "edge" }, { ...job, id: "high", ai_score: 88 },
    { ...job, id: "low", ai_score: 70 },
    { ...job, id: "failed", ai_score_status: "score_failed", ai_score: null },
    { ...job, id: "missing", ai_score: null },
    { ...job, id: "sending", greeting_send_state: "sending" },
    { ...job, id: "ended", job_status: "ended_forced" },
  ];
  assert.deepEqual(jobs.filter((item) => canStartGreeting(item, 71)).map((item) => item.id),
    ["edge", "high"]);
});

test("transitions use the latest threshold and require usable scoring evidence", () => {
  assert.equal(canStartGreeting(job, 72), false);
  assert.equal(canStartGreeting(job, undefined), false);
  assert.equal(canStartGreeting({ ...job, ai_score: Infinity }, 71), false);
  assert.equal(canStartGreeting({ ...job, ai_score: "invalid" }, 71), false);
  assert.equal(canStartGreeting({ ...job, ai_score: "71" }, 71), true);
  assert.equal(canStartGreeting({ ...job, ai_score_status: "not_scored", ai_score: null }, 71), true);
});

test("a confirmed batch override includes only valid low scores", () => {
  const jobs = [
    { ...job, id: "edge" },
    { ...job, id: "low", ai_score: 68 },
    { ...job, id: "zero", ai_score: 0 },
    { ...job, id: "failed", ai_score_status: "score_failed", ai_score: null },
    { ...job, id: "missing", ai_score: null },
    { ...job, id: "invalid", ai_score: "invalid" },
    { ...job, id: "sending", ai_score: 68, greeting_send_state: "sending" },
    { ...job, id: "ended", ai_score: 68, job_status: "ended_forced" },
  ];
  assert.deepEqual(jobs.filter((item) => canOverrideGreetingThreshold(item, 71))
    .map((item) => item.id), ["low", "zero"]);
  assert.deepEqual(jobs.filter((item) => canStartGreeting(item, 71)
    || canOverrideGreetingThreshold(item, 71)).map((item) => item.id),
  ["edge", "low", "zero"]);
});

test("greeting generation includes unscored jobs but skips inactive or locked jobs", () => {
  const jobs = [
    { ...job, id: "unscored", job_status: "greeting_ready", ai_score_status: "not_scored", ai_score: null },
    { ...job, id: "scored", job_status: "greeting_ready" },
    { ...job, id: "failed", job_status: "greeting_ready", ai_score_status: "score_failed" },
    { ...job, id: "below-threshold", ai_score: 65 },
    { ...job, id: "sending", job_status: "greeting_ready", greeting_send_state: "sending" },
    { ...job, id: "unknown", job_status: "greeting_ready", greeting_send_state: "unknown" },
    { ...job, id: "sent", job_status: "greeted", greeting_send_state: "sent" },
    { ...job, id: "ended", job_status: "ended_forced" },
  ];
  assert.deepEqual(jobs.filter(canGenerateGreeting).map((item) => item.id),
    ["unscored", "scored", "failed"]);
});

test("job rows show persisted send failures but do not mislabel uncertain or sent greetings", () => {
  const ready = { job_status: "greeting_ready", greeting_send_state: "idle" };
  assert.deepEqual(greetingSendIssue({ ...ready, greeting_send_note: "输入框未出现" }), {
    label: "上次发送失败", reason: "输入框未出现", className: "error",
  });
  assert.deepEqual(greetingSendIssue({ ...ready, greeting_send_state: "unknown",
    greeting_send_note: "发送后未能确认" }), {
    label: "发送结果待确认", reason: "发送后未能确认", className: "warning",
  });
  assert.equal(greetingSendIssue({ ...ready, greeting_send_note: "已人工确认未发送" }), null);
  assert.equal(greetingSendIssue({ ...ready, job_status: "greeted",
    greeting_send_state: "sent", greeting_send_note: "预设招呼语已发送" }), null);
});
