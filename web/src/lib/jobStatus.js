export const JOB_STATUSES = {
  collected: { label: "已采集", className: "collected" },
  scored: { label: "已评分", className: "scored" },
  filtered: { label: "已评分", className: "scored muted" },
  greeting_ready: { label: "打招呼", className: "greeting_ready" },
  greeted: { label: "已招呼", className: "greeted" },
  monitoring: { label: "已招呼", className: "greeted" },
  ended_monitoring_expired: { label: "已结束（过期）", className: "ended", reason: "过期" },
  ended_rejected: { label: "已结束（拒绝）", className: "ended", reason: "拒绝" },
  ended_applied: { label: "已结束（投递）", className: "ended", reason: "投递" },
  ended_forced: { label: "已结束（强制中断）", className: "ended", reason: "强制中断" },
};

export function statusInfo(value) {
  return JOB_STATUSES[value] || JOB_STATUSES.collected;
}

export function canStartGreeting(job, scoreThreshold) {
  if (job.job_status !== "scored" || job.greeting_send_state !== "idle") return false;
  return job.ai_score_status === "not_scored"
    || (job.ai_score_status === "scored" && job.ai_score != null
      && Number.isFinite(Number(job.ai_score)) && Number.isFinite(scoreThreshold)
      && Number(job.ai_score) >= scoreThreshold);
}

export function canOverrideGreetingThreshold(job, scoreThreshold) {
  return job.job_status === "scored" && job.greeting_send_state === "idle"
    && job.ai_score_status === "scored" && job.ai_score != null
    && Number.isFinite(Number(job.ai_score)) && Number.isFinite(scoreThreshold)
    && Number(job.ai_score) < scoreThreshold;
}

export function canGenerateGreeting(job) {
  return job.job_status === "greeting_ready" && job.greeting_send_state === "idle";
}

export function greetingSendIssue(job) {
  if (job.job_status !== "greeting_ready") return null;
  const note = job.greeting_send_note?.trim();
  if (job.greeting_send_state === "unknown") {
    return { label: "发送结果待确认", reason: note || "请到 BOSS 会话中核查", className: "warning" };
  }
  if (job.greeting_send_state === "idle" && note && note !== "已人工确认未发送") {
    return { label: "上次发送失败", reason: note, className: "error" };
  }
  return null;
}

export function listStatusInfo(job, scoreThreshold) {
  const status = job.job_status;
  const belowThreshold = job.ai_score_status === "scored" &&
    Number.isFinite(scoreThreshold) && job.ai_score != null &&
    Number(job.ai_score) < scoreThreshold;
  const info = status === "greeted" || status === "monitoring" ? JOB_STATUSES.greeted
    : status?.startsWith("ended_") ? { label: "已结束", className: "ended" }
      : status === "greeting_ready" ? JOB_STATUSES.greeting_ready
        : JOB_STATUSES.scored;
  return belowThreshold ? { ...info, className: `${info.className} muted` } : info;
}
