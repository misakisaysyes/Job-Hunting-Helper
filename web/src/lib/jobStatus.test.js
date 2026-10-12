import test from "node:test";
import assert from "node:assert/strict";
import { canForceEnd, canGenerateGreeting, canOverrideGreetingThreshold, canStartGreeting, greetingSendIssue, jobScoreInfo,
  matchesListStatus } from "./jobStatus.js";

const job = {
  job_status: "scored", greeting_send_state: "idle",
  ai_score_status: "scored", ai_score: 71,
};

test("prefiltered jobs show 100 points as a prefilter score", () => {
  assert.deepEqual(jobScoreInfo({ ai_score_status: "not_scored", ai_score: null }, 71), {
    text: "100 分", note: "预筛已评分", className: "score qualified",
  });
  assert.deepEqual(jobScoreInfo({ ai_score_status: "scored", ai_score: 82 }, 71), {
    text: "82 分", className: "score qualified",
  });
  assert.deepEqual(jobScoreInfo({ ai_score_status: "score_failed", ai_score: null }, 71), {
    text: "评分失败", className: "score neutral",
  });
});

test("changed jobs leave the current status list immediately", () => {
  const jobs = [
    { id: "scored", job_status: "scored" },
    { id: "ready", job_status: "greeting_ready" },
    { id: "greeted", job_status: "greeted" },
    { id: "monitoring", job_status: "monitoring" },
    { id: "ended", job_status: "ended_forced" },
  ];
  assert.deepEqual(jobs.filter((item) => matchesListStatus(item, "greeting_ready"))
    .map((item) => item.id), ["ready"]);
  const changed = jobs.map((item) => item.id === "ready"
    ? { ...item, job_status: "greeted" } : item);
  assert.deepEqual(changed.filter((item) => matchesListStatus(item, "greeting_ready")), []);
  assert.deepEqual(changed.filter((item) => matchesListStatus(item, "greeted"))
    .map((item) => item.id), ["ready", "greeted", "monitoring"]);
  assert.deepEqual(changed.filter((item) => matchesListStatus(item, "ended"))
    .map((item) => item.id), ["ended"]);
});

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

test("batch ending uses the same allowed states as single-job ending", () => {
  const statuses = ["scored", "greeting_ready", "greeted", "ended_forced"];
  assert.deepEqual(statuses.filter((job_status) => canForceEnd({ job_status, greeting_send_state: "idle" })),
    ["scored", "greeting_ready"]);
  assert.equal(canForceEnd({ job_status: "greeting_ready", greeting_send_state: "sending" }), false);
  assert.equal(canForceEnd({ job_status: "greeting_ready", greeting_send_state: "sent" }), false);
  assert.equal(canForceEnd({ job_status: "greeted", greeting_send_state: "sent" }), false);
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
