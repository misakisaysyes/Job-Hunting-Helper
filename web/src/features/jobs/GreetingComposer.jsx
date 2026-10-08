import React, { useEffect, useState } from "react";
import { confirmGreetingNotSent, fetchJob, generateGreeting, saveGreeting, sendGreeting } from "../../api/jobs";

const MAX_GREETING_LENGTH = 300;

export default function GreetingComposer({ job, active, onJobChange }) {
  const [draft, setDraft] = useState(job.greeting || "");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => { setDraft(job.greeting || ""); }, [job.source_platform, job.source_job_id, job.greeting]);

  async function run(action, task, success) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      const result = await task();
      success(result);
    } catch (cause) {
      let refreshed = null;
      if (action === "send") {
        try {
          refreshed = await fetchJob(job);
          onJobChange(refreshed);
        } catch { /* Keep the send error visible if refresh fails. */ }
      }
      setError(refreshed?.greeting_send_state === "unknown" ? "" : cause.message || "操作失败");
    } finally {
      setBusy("");
    }
  }

  const canEdit = active && job.greeting_send_state === "idle";
  const canGenerate = active && ["scored", "score_failed"].includes(job.ai_score_status);
  const trimmed = draft.trim();
  const dirty = trimmed !== (job.greeting || "");

  return (
    <section className="detail-card greeting-card">
      <div className="detail-card-heading">
        <strong>招呼语</strong>
        {active && <div className="greeting-actions">
          {canGenerate && <button type="button" className="text-button" onClick={() => {
            if (trimmed && !window.confirm("重新生成会覆盖当前输入的招呼语，确定继续吗？")) return;
            run("generate", () => generateGreeting(job), (result) => {
              onJobChange(result.job);
              setDraft(result.greeting); setNotice("已重新生成并保存，可编辑后发送。");
            });
          }} disabled={!canEdit || Boolean(busy)}>{busy === "generate" ? "生成中…" : "AI 生成"}</button>}
          {canGenerate && <span className="greeting-action-divider" aria-hidden="true" />}
          <button type="button" className="text-button"
            disabled={!canEdit || !trimmed || trimmed.length > MAX_GREETING_LENGTH || Boolean(busy)}
            onClick={() => run("save", () => saveGreeting(job, draft), (result) => {
              onJobChange(result.job); setNotice("招呼语已保存。");
            })}>{busy === "save" ? "保存中…" : "保存"}</button>
          <span className="greeting-action-divider" aria-hidden="true" />
          <button type="button" className="text-button" disabled={!canEdit || !trimmed || trimmed.length > MAX_GREETING_LENGTH || Boolean(busy)}
            onClick={() => {
              if (!window.confirm(`确认向「${job.company || job.title}」的 BOSS 招聘者发送当前招呼语吗？`)) return;
              run("send", () => sendGreeting(job, draft), (result) => {
                onJobChange(result.job); setNotice(result.message || "招呼语已发送。");
              });
            }}>{busy === "send" ? "发送中…" : "发送招呼"}</button>
        </div>}
      </div>
      {active ? <textarea aria-label="编辑招呼语" value={draft} onChange={(event) => {
        setDraft(event.target.value); setNotice("");
      }}
        placeholder="输入招呼语，或使用 AI 生成草稿" maxLength={MAX_GREETING_LENGTH} disabled={!canEdit || Boolean(busy)} />
        : <p>{job.greeting || "此阶段尚无招呼语"}</p>}
      {active && <div className="greeting-footer">
        <span>{notice || (dirty ? "当前内容尚未保存；发送时会一并保存。" : job.greeting ? "已保存" : "尚无招呼语")} · {draft.length}/{MAX_GREETING_LENGTH} 字</span>
      </div>}
      {job.greeting_send_state === "unknown" && <p className="detail-warning">
        {job.greeting_send_note || "发送结果未确认，请先到 BOSS 会话中人工核查；系统不会自动重发。"}
        {active && <button type="button" className="text-button" disabled={Boolean(busy)} onClick={() => {
          if (!window.confirm("已确认 BOSS 会话中没有这条招呼语？解除锁定后可以再次发送，请避免重复发送。")) return;
          run("confirm", () => confirmGreetingNotSent(job), (result) => {
            onJobChange(result.job);
            setNotice(result.job.greeting_send_state === "sent"
              ? "已在 BOSS 会话中确认招呼语。" : "已确认未发送，可以重新发送。");
          });
        }}>{busy === "confirm" ? "处理中…" : "确认未发送"}</button>}
      </p>}
      {job.greeting_send_state === "sending" && <p className="detail-warning">正在发送，请等待结果。</p>}
      {active && job.greeting_send_state == null && <p className="detail-warning">当前 API 未返回招呼语状态，请重启 web/start.sh 后刷新页面。</p>}
      {error && <p className="detail-error" role="alert">{error}</p>}
    </section>
  );
}
