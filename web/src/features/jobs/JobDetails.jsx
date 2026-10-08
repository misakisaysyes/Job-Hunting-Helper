import React, { lazy, Suspense, useEffect, useState } from "react";
import { forceEnd, openConversation, startGreeting } from "../../api/jobs";
import { statusInfo } from "../../lib/jobStatus";
import GreetingComposer from "./GreetingComposer";
import "./details.css";

const StatusFlow = lazy(() => import("../../components/StatusFlow"));
const RECRUITMENT_TYPE_LABELS = {
  experienced: "社招",
  campus: "校招",
  internship: "实习",
};

function originalJobLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export default function JobDetails({ job, scoreThreshold, onJobChange }) {
  const [selected, setSelected] = useState(job.job_status);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [chatNotice, setChatNotice] = useState("");
  const link = originalJobLink(job.url);
  const hasConversation = job.source_platform === "boss"
    && job.greeting_send_state === "sent" && Boolean(job.chat_url);
  const active = selected === job.job_status;
  const scoreStage = selected === "scored";
  const ended = selected.startsWith("ended_");
  const belowThreshold = job.ai_score_status === "scored" && job.ai_score != null &&
    Number.isFinite(scoreThreshold) && Number(job.ai_score) < scoreThreshold;

  useEffect(() => {
    setSelected(job.job_status);
    setChatNotice("");
  }, [job.source_platform, job.source_job_id, job.job_status]);

  async function advance(action, task) {
    setBusy(action);
    setError("");
    try {
      const result = await task();
      onJobChange(result.job);
    } catch (cause) {
      setError(cause.message || "状态更新失败");
    } finally {
      setBusy("");
    }
  }

  function confirmForceEnd() {
    if (!window.confirm(`确定强制结束「${job.title || job.source_job_id}」的流程吗？`)) return;
    advance("force", () => forceEnd(job));
  }

  function selectStage(status) {
    if (busy) return;
    if (status === "greeting_ready" && job.job_status === "scored" && !belowThreshold) {
      advance("start", () => startGreeting(job));
    } else {
      setSelected(status);
    }
  }

  async function showConversation() {
    setBusy("chat");
    setError("");
    setChatNotice("");
    try {
      const result = await openConversation(job);
      setChatNotice(result.message);
    } catch (cause) {
      setError(cause.message || "打开会话失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="job-details">
      <section className="detail-card">
        <div className="detail-card-heading"><strong>岗位描述</strong>
          {link && <a href={link} target="_blank" rel="noopener noreferrer">查看原岗 ↗</a>}
        </div>
        <p className="job-description">{job.jd || "暂无岗位描述"}</p>
        <div className="details-meta">招聘者：{job.hr_name || "未知"}　·　招聘类型：{RECRUITMENT_TYPE_LABELS[job.recruitment_type] || "未知"}　·　公司规模：{job.company_size || "未知"}</div>
      </section>

      <section className="detail-card workflow-card">
        <div className="detail-card-heading workflow-heading">
          <strong>岗位状态</strong>
          {["scored", "greeting_ready", "greeted"].includes(job.job_status) &&
            <button type="button" className="delete-button"
              onClick={confirmForceEnd} disabled={Boolean(busy) || job.greeting_send_state === "sending"}>
              {busy === "force" ? "结束中…" : "结束流程"}
            </button>}
        </div>
        <Suspense fallback={<div className="status-flow-loading">加载状态图…</div>}>
          <StatusFlow job={job} selected={selected} belowThreshold={belowThreshold} onSelect={selectStage} />
        </Suspense>
        <div className="stage-content">
          {scoreStage && <>
            {job.ai_score_status === "scored" && <section className="score-reason">
              <div className="detail-card-heading">
                <strong>AI 评分与理由</strong>
                {belowThreshold && active && <button type="button" className="text-button"
                  onClick={() => advance("start", () => startGreeting(job))} disabled={Boolean(busy)}>
                  {busy === "start" ? "处理中…" : "仍去打招呼"}
                </button>}
              </div>
              <p>{job.ai_score != null ? `${job.ai_score} 分 · ` : ""}{job.ai_score_reason || "暂无评分理由"}</p>
            </section>}
            {job.ai_score_status === "score_failed" && <section className="score-reason">
              <div className="detail-card-heading"><strong>评分失败</strong>
              </div>
              <p>{job.ai_score_error || "暂无错误信息"}</p>
            </section>}
          </>}
          {selected === "greeting_ready" &&
            <GreetingComposer job={job} active={active} onJobChange={onJobChange} />}
          {selected === "greeted" && <section className="score-reason greeting-sent-card">
            <div className="detail-card-heading"><strong>招呼语</strong>
              {hasConversation && <button type="button" className="text-button"
                onClick={showConversation} disabled={Boolean(busy)}>
                {busy === "chat" ? "打开中…" : "查看会话 ↗"}
              </button>}
            </div>
            <p>{job.greeting || "暂无招呼语记录"}</p>
            {chatNotice && <p className="detail-notice" role="status">{chatNotice}</p>}
          </section>}
          {ended && <section className="end-reason">
            <strong>结束原因</strong>
            <p>{statusInfo(selected).reason || "未记录"}</p>
          </section>}
          {error && <p className="detail-error" role="alert">{error}</p>}
        </div>
      </section>
    </div>
  );
}
