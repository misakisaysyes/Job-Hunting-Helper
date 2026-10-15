import test from "node:test";
import assert from "node:assert/strict";
import { prepareGreetingJob } from "./prepareGreetingBatch.js";

test("scored jobs are transitioned before a greeting is generated", async () => {
  const steps = [];
  const job = { source_job_id: "scored", job_status: "scored" };
  const result = await prepareGreetingJob(job, {
    allowBelowThreshold: true,
    startGreeting: async (item, options) => {
      steps.push(["start", item.job_status, options.allow_below_threshold]);
      return { job: { ...item, job_status: "greeting_ready" } };
    },
    generateGreeting: async (item) => {
      steps.push(["generate", item.job_status]);
      return { job: { ...item, greeting: "草稿" } };
    },
    onJobChange: (item) => steps.push(["updated", item.job_status]),
  });
  assert.deepEqual(result, { transitioned: true, generated: true });
  assert.deepEqual(steps, [
    ["start", "scored", true], ["updated", "greeting_ready"],
    ["generate", "greeting_ready"], ["updated", "greeting_ready"],
  ]);
});

test("jobs already ready for greetings skip the transition", async () => {
  const job = { source_job_id: "ready", job_status: "greeting_ready" };
  let startCalled = false;
  const result = await prepareGreetingJob(job, {
    startGreeting: async () => { startCalled = true; },
    generateGreeting: async () => ({ job: { ...job, greeting: "新草稿" } }),
    onJobChange: () => {},
  });
  assert.deepEqual(result, { transitioned: false, generated: true });
  assert.equal(startCalled, false);
});

test("generation failure keeps a completed transition visible", async () => {
  const job = { source_job_id: "partial", job_status: "scored" };
  const updated = [];
  const result = await prepareGreetingJob(job, {
    startGreeting: async () => ({ job: { ...job, job_status: "greeting_ready" } }),
    generateGreeting: async () => { throw new Error("AI 请求失败"); },
    onJobChange: (item) => updated.push(item.job_status),
  });
  assert.equal(result.transitioned, true);
  assert.equal(result.generated, false);
  assert.match(result.error.message, /AI 请求失败/);
  assert.deepEqual(updated, ["greeting_ready"]);
});

test("a failed transition never starts greeting generation", async () => {
  let generateCalled = false;
  const result = await prepareGreetingJob({ job_status: "scored" }, {
    startGreeting: async () => { throw new Error("状态已变化"); },
    generateGreeting: async () => { generateCalled = true; },
    onJobChange: () => {},
  });
  assert.equal(result.transitioned, false);
  assert.equal(result.generated, false);
  assert.equal(generateCalled, false);
});
