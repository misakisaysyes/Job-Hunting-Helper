const TERMINAL_STATUSES = new Set([
  "completed", "completed_with_shortage", "failed", "stopped", "interrupted",
]);
const TASK_TYPES = ["collection", "monitoring", "monitoring_followup", "monitoring_delete"];

export function currentTaskRuns(data) {
  return Object.fromEntries(TASK_TYPES.map((type) => [type, data.current?.[type] ?? null]));
}

export function newlyCompletedRuns(previous, current) {
  if (!previous) return [];
  return TASK_TYPES.flatMap((type) => {
    const before = previous[type];
    const after = current[type];
    if (after?.run_id == null || !TERMINAL_STATUSES.has(after.status)) return [];
    return before?.run_id !== after.run_id || !TERMINAL_STATUSES.has(before.status)
      ? [`${type}:${after.run_id}`] : [];
  });
}

export function hasNewTaskCompletion(previous, current) {
  return newlyCompletedRuns(previous, current).length > 0;
}
