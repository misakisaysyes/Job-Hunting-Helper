export function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

export function savedFollowupText(conversation) {
  return conversation.followup_text || conversation.last_followup_text || conversation.greeting_text || "";
}

export function followupHint(conversation) {
  if (conversation.followup_cooldown_until) {
    return `追问冷却中，预计 ${formatTime(conversation.followup_cooldown_until)} 解冻。`;
  }
  return conversation.followup_reason || "当前没有可发送的追问语";
}
