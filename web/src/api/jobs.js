import { jsonOptions, requestJson } from "./client";

function jobUrl(job) {
  return `/api/jobs/${encodeURIComponent(job.source_platform)}/${encodeURIComponent(job.source_job_id)}`;
}

export const generateGreeting = (job) =>
  requestJson(`${jobUrl(job)}/greeting/generate`, jsonOptions("POST"));

export const scoreJob = (job) =>
  requestJson(`${jobUrl(job)}/score`, jsonOptions("POST"));

export const startGreeting = (job) =>
  requestJson(`${jobUrl(job)}/greeting/start`, jsonOptions("POST"));

export const saveGreeting = (job, greeting) =>
  requestJson(`${jobUrl(job)}/greeting`, jsonOptions("PUT", { greeting }));

export const sendGreeting = (job, greeting) =>
  requestJson(`${jobUrl(job)}/greeting/send`, jsonOptions("POST", { greeting }));

export const confirmGreetingNotSent = (job) =>
  requestJson(`${jobUrl(job)}/greeting/confirm-not-sent`, jsonOptions("POST", { confirmed: true }));

export const openConversation = (job) =>
  requestJson(`${jobUrl(job)}/chat/open`, jsonOptions("POST"));

export const forceEnd = (job) =>
  requestJson(`${jobUrl(job)}/force-end`, jsonOptions("POST"));

export const deleteJob = (job) => requestJson(jobUrl(job), { method: "DELETE" });

export const fetchJob = (job) => requestJson(jobUrl(job));
