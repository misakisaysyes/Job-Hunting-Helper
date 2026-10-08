import { useEffect, useRef } from "react";
import { getTodayTasks } from "../api/tasks";
import { currentTaskRuns, hasNewTaskCompletion } from "../lib/taskCompletion";
import { useAlertSound } from "./useAlertSound";

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
        if (hasNewTaskCompletion(previousRuns.current, current)) playConfirmationAlert();
        previousRuns.current = current;
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
