import assert from "node:assert/strict";
import test from "node:test";
import { monitoringRunsForPhase, phasesForMonitoringRun } from "./monitoringRuns.js";

test("individual monitoring runs belong to one scan section", () => {
  const history = [
    { id: 1, counts: { phase: "communicated" } },
    { id: 2, counts: { phase: "exchanged" } },
    { id: 3, counts: { phase: "new_greetings" } },
  ];
  assert.deepEqual(monitoringRunsForPhase(history, "communicated").map((run) => run.id), [1]);
  assert.deepEqual(monitoringRunsForPhase(history, "exchanged").map((run) => run.id), [2]);
  assert.deepEqual(monitoringRunsForPhase(history, "new_greetings").map((run) => run.id), [3]);
});

test("legacy combined scans appear in both original scan sections", () => {
  assert.deepEqual(phasesForMonitoringRun({ counts: {} }), ["communicated", "new_greetings"]);
  assert.deepEqual(phasesForMonitoringRun({ counts: { phase: "all" } }), ["communicated", "new_greetings"]);
});
