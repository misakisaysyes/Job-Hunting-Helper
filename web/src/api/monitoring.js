import { jsonOptions, requestJson } from "./client";

export function getCurrentMonitoring() {
  return requestJson("/api/monitoring/current");
}

export function startMonitoring() {
  return requestJson("/api/monitoring", jsonOptions("POST"));
}

export function listConversations(limit, offset, filters = {}) {
  const params = new URLSearchParams({ limit, offset });
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return requestJson(`/api/conversations?${params}`);
}

export function listFilterCandidates(limit, offset, filters = {}) {
  const params = new URLSearchParams({ limit, offset });
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return requestJson(`/api/filter-candidates?${params}`);
}

export function deleteConversationRecord(platform, conversationId) {
  return requestJson(`/api/conversations/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}`, jsonOptions("DELETE"));
}

export function deleteFilterRecord(platform, conversationId) {
  return requestJson(`/api/filter-candidates/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}`, jsonOptions("DELETE"));
}

export function confirmFilterDelete(platform, conversationId, confirmStillPresent = false) {
  return requestJson(`/api/filter-candidates/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}/delete/confirm`,
    jsonOptions("POST", { confirm_still_present: confirmStillPresent }));
}

export function openConversation(platform, conversationId) {
  return requestJson(`/api/conversations/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}/open`, jsonOptions("POST"));
}

export function openFilterCandidate(platform, conversationId) {
  return requestJson(`/api/filter-candidates/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}/open`, jsonOptions("POST"));
}

export function sendFollowup(platform, conversationId) {
  return requestJson(`/api/conversations/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}/followup/send`, jsonOptions("POST"));
}

export function terminateMonitoring(platform, conversationId) {
  return requestJson(`/api/conversations/${encodeURIComponent(platform)}/${encodeURIComponent(conversationId)}/monitoring/terminate`, jsonOptions("POST"));
}
