const TERMINAL_STATUSES = new Set([
  "completed", "completed_with_shortage", "failed", "stopped", "interrupted",
]);

export function currentTaskRuns(data) {
  return {
    collection: data.current?.collection ?? null,
    monitoring: data.current?.monitoring ?? null,
  };
}

export function hasNewTaskCompletion(previous, current) {
  if (!previous) return false;
  return ["collection", "monitoring"].some((type) => {
    const before = previous[type];
    const after = current[type];
    if (after?.run_id == null || !TERMINAL_STATUSES.has(after.status)) return false;
    return before?.run_id !== after.run_id || !TERMINAL_STATUSES.has(before.status);
  });
}
