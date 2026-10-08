import { jsonOptions, requestJson } from "./client";

export const getBasicSettings = () => requestJson("/api/settings/basic");
export const saveBasicSettings = (settings) =>
  requestJson("/api/settings/basic", jsonOptions("PUT", settings));

export const getFullSettings = () => requestJson("/api/settings/full");
export const saveFullSettings = (settings) =>
  requestJson("/api/settings/full", jsonOptions("PUT", settings));
export const applyFullSettings = () =>
  requestJson("/api/settings/full/apply", jsonOptions("POST", {}));

export const uploadResume = (file) =>
  requestJson(`/api/settings/resume?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
    body: file,
  });
