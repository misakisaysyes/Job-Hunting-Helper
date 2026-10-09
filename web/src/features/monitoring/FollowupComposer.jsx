import { useState } from "react";
import { generateFollowup, getFollowup, saveFollowup } from "../../api/monitoring";
import { followupHint, savedFollowupText } from "./conversationDisplay";

const MAX_FOLLOWUP_LENGTH = 300;

export default function FollowupComposer({ conversation, draft, onDraftChange, onChange,
  onSend, onBusyChange, disabled }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const canEdit = conversation.followup_status === "pending_review" && conversation.followup_eligible
    && !conversation.followup_cooldown_until;
  const locked = Boolean(busy || disabled);
  const trimmed = draft.trim();
  const valid = Boolean(trimmed) && trimmed.length <= MAX_FOLLOWUP_LENGTH;
  const dirty = trimmed !== (conversation.followup_text || "");
  const options = { anchor_id: conversation.followup_anchor_id, expected_text: conversation.followup_text };

  async function run(action, task, success) {
    if (locked) return;
    setBusy(action);
    onBusyChange(action);
    setError("");
    setNotice("");
    try {
      const result = await task();
      if (result?.conversation) onChange(result.conversation);
      if (success) setNotice(success);
    } catch (cause) {
      setError(cause.message || "追问操作失败");
      try {
        const result = await getFollowup(conversation.platform, conversation.conversation_id);
        onChange(result.conversation, { preserveDraft: true });
      } catch { /* Preserve the original action error if the conversation disappeared. */ }
    } finally {
      setBusy("");
      onBusyChange("");
    }
  }

  return <section className="detail-card monitoring-followup-card">
    <div className="detail-card-heading">
      <strong>原招呼语 / 追问语</strong>
      {canEdit && <div className="greeting-actions">
        <button className="text-button" type="button" disabled={locked || !conversation.followup_ai_available}
          title={conversation.followup_ai_reason || undefined} onClick={() => {
            if (trimmed && !window.confirm("重新生成会覆盖当前追问语，确定继续吗？")) return;
            void run("generate", () => generateFollowup(conversation.platform, conversation.conversation_id, options),
              "已重新生成并保存，可编辑后发送。");
          }}>{busy === "generate" ? "生成中…" : "AI生成"}</button>
        <span className="greeting-action-divider" aria-hidden="true" />
        <button className="text-button" type="button" disabled={locked || !valid}
          onClick={() => void run("save", () => saveFollowup(conversation.platform, conversation.conversation_id,
            { ...options, text: draft }), "追问语已保存。")}>{busy === "save" ? "保存中…" : "保存"}</button>
        <span className="greeting-action-divider" aria-hidden="true" />
        <button className="text-button" type="button" disabled={locked || !valid}
          onClick={() => {
            if (!window.confirm(`确认向「${conversation.company || conversation.recruiter || conversation.conversation_id}」发送当前追问语吗？`)) return;
            void run("send", () => onSend(conversation, trimmed), "追问已发送。");
          }}>{busy === "send" ? "发送中…" : "去追问"}</button>
      </div>}
    </div>
    {canEdit ? <textarea aria-label="编辑追问语" value={draft} maxLength={MAX_FOLLOWUP_LENGTH}
      disabled={locked} placeholder="输入追问语，或使用AI生成草稿"
      onChange={(event) => { onDraftChange(event.target.value); setNotice(""); setError(""); }} />
      : <p>{savedFollowupText(conversation) || "未找到我方原招呼语"}</p>}
    {canEdit && <div className="greeting-footer">
      <span>{notice || (dirty ? "当前内容尚未保存；发送时会一并保存。" : "已保存")} · {draft.length}/{MAX_FOLLOWUP_LENGTH} 字</span>
    </div>}
    {!canEdit && <p className="detail-warning">{followupHint(conversation)}</p>}
    {!canEdit && conversation.followup_cooldown_until && conversation.followup_reason
      && !conversation.followup_reason.startsWith("距离上次招呼语或追问不足 ")
      && <p className="detail-warning">{conversation.followup_reason}</p>}
    {canEdit && !conversation.followup_ai_available && <p className="detail-warning">{conversation.followup_ai_reason}</p>}
    {notice && !canEdit && <p className="detail-notice" role="status">{notice}</p>}
    {error && <p className="detail-error" role="alert">{error}</p>}
  </section>;
}
