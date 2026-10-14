import { useEffect, useRef } from "react";
import { getTodayTasks } from "../api/tasks";
import { currentTaskRuns, newlyCompletedRuns } from "../lib/taskCompletion";
import { useAlertSound } from "./useAlertSound";

const CLAIM_KEY = "job-hunting-helper:completion-alerts:v1";
const LOCK_KEY = "job-hunting-helper:completion-alert";

function claimCompletedRuns(runKeys) {
  try {
    const stored = JSON.parse(window.localStorage.getItem(CLAIM_KEY) || "[]");
    const seen = new Set(Array.isArray(stored) ? stored : []);
    const fresh = runKeys.filter((key) => !seen.has(key));
    if (!fresh.length) return false;
    window.localStorage.setItem(CLAIM_KEY, JSON.stringify([...seen, ...fresh].slice(-80)));
    return true;
  } catch {
    // Storage may be disabled; the active tab still gets its completion alert.
    return true;
  }
}

export function useTaskCompletionAlert() {
  const { playConfirmationAlert } = useAlertSound();
  const previousRuns = useRef(null);

  useEffect(() => {
    let active = true;
    let timer;
    async function poll() {
      try {
        const tasks = await getTodayTasks();
        if (!active) return;
        const current = currentTaskRuns(tasks);
        const completed = newlyCompletedRuns(previousRuns.current, current);
        previousRuns.current = current;
        if (completed.length) {
          const alertOnce = () => {
            if (active && claimCompletedRuns(completed)) playConfirmationAlert();
          };
          if (navigator.locks?.request) {
            try { await navigator.locks.request(LOCK_KEY, alertOnce); }
            catch { alertOnce(); }
          } else alertOnce();
        }
      } catch {
        // Keep the last observed state, so a temporary request failure loses no alert.
      } finally {
        if (active) timer = window.setTimeout(poll, 2000);
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [playConfirmationAlert]);
}
