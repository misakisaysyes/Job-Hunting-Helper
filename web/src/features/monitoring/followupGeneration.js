export function canGenerateFollowup(conversation) {
  return conversation.followup_status === "pending_review"
    && Boolean(conversation.followup_eligible && conversation.followup_ai_available)
    && !conversation.followup_cooldown_until;
}

export function createFollowupGenerationAction(generate, onChange) {
  return {
    label: "生成追问语", parallel: true,
    eligible: canGenerateFollowup,
    confirm: (count, skipped) => `确定为所选 ${count} 条会话生成追问语吗？已有草稿和未保存的编辑会被覆盖。${skipped ? `另有 ${skipped} 条不可生成的会话将跳过。` : ""}`,
    async perform(item) {
      const result = await generate(item.platform, item.conversation_id, {
        anchor_id: item.followup_anchor_id, expected_text: item.saved_followup_text,
      });
      onChange(result.conversation);
    },
  };
}
