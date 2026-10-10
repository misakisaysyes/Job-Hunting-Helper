import { Fragment } from "react";
import MonitoringBulkActions from "./MonitoringBulkActions";
import { monitoringKey } from "./useMonitoringBatch";
import { formatTime } from "./conversationDisplay";

function display(value) {
  return value == null || value === "" ? "—" : String(value);
}

const CONVERSATION_STATUS_LABELS = {
  unread: "未读",
  read_no_reply: "已读未回",
};

export default function ConversationList({ conversations, total, page, pageSize, loading, onPage, onRefresh, expanded, onExpanded, renderDetails, onDelete, busyAction, busyDelete, filtered, batch, onBatchModeChange }) {
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min(total, (page + 1) * pageSize);
  return (
    <section className="panel" aria-label="会话列表">
      <div className="panel-head"><span>会话列表</span><span className="range">{total ? `${start}–${end} / ${total}` : "暂无记录"}</span></div>
      <div className="monitoring-batch-toolbar"><button type="button" className="toolbar-button"
        disabled={loading || Boolean(batch.busy) || Boolean(busyAction)} onClick={onRefresh}>
        {loading ? "刷新中…" : "刷新列表"}
      </button><button type="button" className="toolbar-button"
        disabled={loading || Boolean(batch.busy) || Boolean(busyAction)}
        onClick={() => onBatchModeChange(!batch.batchMode)}>
        {batch.batchMode ? "退出批量操作" : "批量操作"}
      </button></div>
      {loading && conversations.length === 0 ? (
        <div className="empty">正在加载会话…</div>
      ) : !loading && conversations.length === 0 ? (
        <div className="empty monitoring-empty"><div className="empty-icon">□</div>
          <strong>{filtered ? "没有符合筛选条件的会话" : "暂无会话数据"}</strong>
          {!filtered && <span>启动监测后，扫描到的会话会显示在这里。</span>}</div>
      ) : (
        <div className="table-scroll">
          <table className={`monitoring-table monitoring-conversation-table${batch.batchMode ? " monitoring-batch-table" : ""}`}>
            <thead><tr>
              {batch.batchMode && <th className="select-cell"><input type="checkbox" aria-label="全选本页会话"
                checked={batch.allSelected} disabled={loading || Boolean(batch.busy) || !conversations.length}
                onChange={(event) => batch.selectAll(event.target.checked)} /></th>}
              <th>岗位 / 公司 / HR</th>
              <th>HR检阅状态</th>
              <th>追问状态</th>
              <th>更新时间<small>入库时间 / 更新时间</small></th>
              <th>操作</th>
            </tr></thead>
            <tbody>{conversations.map((conversation) => {
              const key = monitoringKey(conversation);
              return <Fragment key={key}><tr>
                {batch.batchMode && <td className="select-cell"><input type="checkbox"
                  aria-label={`选择会话 ${conversation.company || conversation.recruiter || conversation.conversation_id}`}
                  checked={batch.selectedKeys.has(key)} disabled={loading || Boolean(batch.busy)}
                  onChange={() => batch.toggle(conversation)} /></td>}
                <td className="position-cell">
                  <div className="job-title">{display(conversation.job_title)}</div>
                  <div className="company">{display(conversation.company)} <span className="dot">·</span> {display(conversation.recruiter)}</div>
                  <div className="job-id">{display(conversation.platform)} · {display(conversation.conversation_id)} <span className="dot">·</span> {conversation.in_job_pool ? "池内" : "池外"}</div>
                </td>
                <td>{CONVERSATION_STATUS_LABELS[conversation.conversation_status || conversation.judgment] || display(conversation.conversation_status || conversation.judgment)}</td>
                <td className="followup-availability-cell">
                  {conversation.followup_cooldown_until ? <>
                    <span className="followup-availability cooling">冷冻中</span>
                    <small>预计 {formatTime(conversation.followup_cooldown_until)} 解冻</small>
                  </> : conversation.followup_eligible
                    ? <span className="followup-availability available">可追问</span>
                    : <span title={conversation.followup_reason || undefined}>—</span>}
                </td>
                <td className="date-cell">
                  <div className="time-block"><span>入库时间</span><time dateTime={conversation.first_seen_at || undefined}>{formatTime(conversation.first_seen_at)}</time></div>
                  <div className="time-block"><span>更新时间</span><time dateTime={conversation.updated_at || undefined}>{formatTime(conversation.updated_at)}</time></div>
                </td>
                <td><div className="conversation-actions">
                  <button className="text-button" type="button"
                    disabled={Boolean(busyAction || batch.busy)} aria-expanded={expanded === key}
                    aria-controls={`conversation-details-${key}`}
                    onClick={() => onExpanded(expanded === key ? "" : key)}>{expanded === key ? "收起" : "详情"}</button>
                  <button className="text-button danger-action" type="button" disabled={Boolean(busyAction || batch.busy)}
                    onClick={() => onDelete(conversation)}>
                    {busyDelete === key ? "删除中…" : "删除"}
                  </button>
                </div></td>
              </tr>
                {expanded === key && <tr className="details-row"><td colSpan={batch.batchMode ? 6 : 5}
                  id={`conversation-details-${key}`}>{renderDetails(conversation)}</td></tr>}
              </Fragment>
            })}</tbody>
          </table>
        </div>
      )}
      <div className="pagination"><span>{total ? `显示 ${start}–${end} 条` : "显示 0 条"}</span>
        <div><button type="button" onClick={() => onPage(page - 1)} disabled={loading || Boolean(batch.busy) || page === 0}>上一页</button>
          <span>第 {page + 1} 页</span>
          <button type="button" onClick={() => onPage(page + 1)} disabled={loading || Boolean(batch.busy) || end >= total}>下一页</button></div>
      </div>
      {batch.batchMode && <MonitoringBulkActions label="会话" count={batch.selectedItems.length} batch={batch}
        actions={[{ key: "generate", label: "批量生成追问语" }, { key: "followup", label: "批量追问" }, { key: "terminate", label: "批量终止会话" },
          { key: "delete", label: "批量删除", danger: true }]} onClose={batch.close} />}
    </section>
  );
}
