const MAX_FOLLOWUP_LENGTH = 300;

export function canSubmitFollowup(item) {
  if (item.followup_status !== "pending_review" || item.followup_display_status !== "available"
      || typeof item.followup_text !== "string" || typeof item.saved_followup_text !== "string") {
    return false;
  }
  const text = item.followup_text.trim();
  if (text.length > MAX_FOLLOWUP_LENGTH) return false;
  // Empty edits are submitted so the server can record that row as failed and continue.
  return !text || (typeof item.followup_anchor_id === "string"
    && item.followup_anchor_id.length > 0 && !/\D/.test(item.followup_anchor_id));
}

export function createFollowupSendingAction(send) {
  return {
    label: "追问", preview: true, stopOnFailure: true,
    batchAction: "followup",
    eligible: canSubmitFollowup,
    toBatchItem: (item) => ({ platform: item.platform, conversation_id: item.conversation_id,
      text: item.followup_text, anchor_id: item.followup_anchor_id, expected_text: item.saved_followup_text }),
    perform: (item) => send(item.platform, item.conversation_id, {
      text: item.followup_text, anchor_id: item.followup_anchor_id, expected_text: item.saved_followup_text,
    }),
  };
}
