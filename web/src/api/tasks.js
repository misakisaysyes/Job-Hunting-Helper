import { requestJson } from "./client";

export const getTodayTasks = () => requestJson("/api/tasks/today");

export function getTaskHistory(date = "") {
  const query = date ? `?${new URLSearchParams({ date })}` : "";
  return requestJson(`/api/tasks/history${query}`);
}
