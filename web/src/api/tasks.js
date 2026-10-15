import { jsonOptions, requestJson } from "./client";

export const getTodayTasks = () => requestJson("/api/tasks/today");

export const getSavedConversionFlow = () => requestJson("/api/workbench/conversion/latest");

export const generateConversionFlow = (start, end) =>
  requestJson("/api/workbench/conversion", jsonOptions("POST", { start, end }));

export const refreshConversionCharts = (start, end) =>
  requestJson("/api/workbench/conversion/refresh", jsonOptions("POST", { start, end }));

export function getTaskHistory(date = "") {
  const query = date ? `?${new URLSearchParams({ date })}` : "";
  return requestJson(`/api/tasks/history${query}`);
}
