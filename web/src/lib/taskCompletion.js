const TERMINAL_STATUSES = new Set([
  "completed", "completed_with_shortage", "failed", "stopped", "interrupted",
]);
const TASK_TYPES = ["collection", "monitoring", "monitoring_followup", "monitoring_delete"];

export function currentTaskRuns(data) {
  return Object.fromEntries(TASK_TYPES.map((type) => [type, data.current?.[type] ?? null]));
}

export function hasNewTaskCompletion(previous, current) {
  if (!previous) return false;
  return TASK_TYPES.some((type) => {
    const before = previous[type];
    const after = current[type];
    if (after?.run_id == null || !TERMINAL_STATUSES.has(after.status)) return false;
    return before?.run_id !== after.run_id || !TERMINAL_STATUSES.has(before.status);
  });
}
