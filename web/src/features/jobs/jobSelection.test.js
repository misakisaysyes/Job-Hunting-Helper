import test from "node:test";
import assert from "node:assert/strict";
import { jobKey, selectPageJobs, toggleSelectedJob } from "./jobSelection.js";

const firstPage = [
  { source_platform: "boss", source_job_id: "one" },
  { source_platform: "boss", source_job_id: "two" },
];
const secondPage = [
  { source_platform: "boss", source_job_id: "three" },
];

test("pagination keeps selections from earlier pages", () => {
  let selected = toggleSelectedJob(new Map(), firstPage[0]);
  selected = selectPageJobs(selected, secondPage, true);
  assert.deepEqual([...selected.keys()], [jobKey(firstPage[0]), jobKey(secondPage[0])]);
  assert.equal(selected.has(jobKey(firstPage[0])), true);

  selected = selectPageJobs(selected, secondPage, false);
  assert.deepEqual([...selected.keys()], [jobKey(firstPage[0])]);
  selected = selectPageJobs(selected, firstPage, true);
  assert.deepEqual([...selected.keys()], [jobKey(firstPage[0]), jobKey(firstPage[1])]);
});
