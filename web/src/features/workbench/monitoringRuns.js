export const MONITORING_PHASES = [
  { phase: "communicated", title: "监测-仅沟通" },
  { phase: "exchanged", title: "监测-有交换" },
  { phase: "new_greetings", title: "监测-新会话" },
];

export function phasesForMonitoringRun(run) {
  const phase = run?.counts?.phase;
  if (MONITORING_PHASES.some((item) => item.phase === phase)) return [phase];
  if (phase === "conversion") return ["communicated", "exchanged"];
  // Runs from before individual phase buttons (and the legacy "all" mode)
  // scanned both top-level tabs together.
  return ["communicated", "new_greetings"];
}

export function monitoringRunsForPhase(history, phase) {
  return history.filter((run) => phasesForMonitoringRun(run).includes(phase));
}
