const TERMINAL_STATUSES = new Set([
  "completed", "completed_with_shortage", "failed", "stopped", "interrupted",
]);
const CLAIM_KEY = "job-hunting-helper:completion-alerts:v1";
const LOCK_KEY = "job-hunting-helper:completion-alert";

// Each task owner observes its own status endpoint and plays the sound only after
// a run it saw running reaches a terminal state. Historical runs stay silent.
export function createTaskCompletionTracker() {
  let runningRunId = null;
  const alertedRunIds = new Set();

  return {
    observe(state) {
      const runId = state?.run_id;
      if (runId == null) return false;
      if (state.status === "running") {
        if (!alertedRunIds.has(runId)) runningRunId = runId;
        return false;
      }
      if (runId !== runningRunId || !TERMINAL_STATUSES.has(state.status)
          || alertedRunIds.has(runId)) return false;
      alertedRunIds.add(runId);
      runningRunId = null;
      return true;
    },
  };
}

// The task owner calls this only after observing its run finish. Keep the
// existing per-origin claim so two open tabs do not both play for that run.
export async function claimTaskCompletion(runKey, { storage, locks } = {}) {
  if (!runKey) return false;
  const claim = () => {
    try {
      const targetStorage = storage === undefined ? globalThis.window?.localStorage : storage;
      if (!targetStorage) return true;
      const stored = JSON.parse(targetStorage.getItem(CLAIM_KEY) || "[]");
      const seen = new Set(Array.isArray(stored) ? stored : []);
      if (seen.has(runKey)) return false;
      targetStorage.setItem(CLAIM_KEY, JSON.stringify([...seen, runKey].slice(-80)));
      return true;
    } catch {
      // Storage may be disabled; the task owner can still play its alert.
      return true;
    }
  };
  let lockManager = locks;
  if (lockManager === undefined) {
    try { lockManager = globalThis.navigator?.locks; } catch { /* Use storage only. */ }
  }
  if (lockManager?.request) {
    try { return await lockManager.request(LOCK_KEY, claim); }
    catch { return claim(); }
  }
  return claim();
}
