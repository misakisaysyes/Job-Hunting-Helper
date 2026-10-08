import { requestJson } from "./client";

export const getTodayTasks = () => requestJson("/api/tasks/today");
