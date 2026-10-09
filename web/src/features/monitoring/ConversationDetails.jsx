import FollowupComposer from "./FollowupComposer";
import { monitoringKey } from "./useMonitoringBatch";
import "../jobs/details.css";

const RECRUITMENT_TYPE_LABELS = { experienced: "社招", campus: "校招", internship: "实习" };

function originalJobLink(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export default function ConversationDetails({ conversation, draft, onDraftChange, onChange,
  onSend, onBusyChange, onOpen, openingConversation, onTerminate, terminatingConversation, disabled }) {
  const job = conversation.job || {};
  const link = originalJobLink(job.url);
  const key = monitoringKey(conversation);
  return <div className="job-details monitoring-conversation-details">
    <section className="detail-card">
      <div className="detail-card-heading"><strong>岗位描述</strong>
        <div className="conversation-actions" role="group" aria-label="会话操作">
          {link && <a href={link} target="_blank" rel="noopener noreferrer">查看原岗 ↗</a>}
          <button className="text-button" type="button" onClick={() => onOpen(conversation)}
            disabled={disabled || Boolean(openingConversation)}>
            {openingConversation === key ? "打开中…" : "查看会话 ↗"}
          </button>
          <button className="text-button danger-action" type="button" onClick={() => onTerminate(conversation)}
            disabled={disabled || Boolean(openingConversation)}>
            {terminatingConversation === key ? "终止中…" : "终止会话"}
          </button>
        </div>
      </div>
      <p className="job-description">{job.jd || "暂未采集该岗位描述"}</p>
      <div className="details-meta">招聘者：{job.hr_name || conversation.recruiter || "未知"}　·　招聘类型：{RECRUITMENT_TYPE_LABELS[job.recruitment_type] || "未知"}　·　公司规模：{job.company_size || "未知"}</div>
    </section>
    <FollowupComposer conversation={conversation} draft={draft} onDraftChange={onDraftChange}
      onChange={onChange} onSend={onSend} onBusyChange={onBusyChange}
      disabled={disabled} />
  </div>;
}
