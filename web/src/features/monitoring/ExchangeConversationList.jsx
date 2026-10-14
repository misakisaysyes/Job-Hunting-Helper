import MonitoringBulkActions from "./MonitoringBulkActions";
import { monitoringKey } from "./monitoringSelection";

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

export default function ExchangeConversationList({ conversations, total, page, pageSize, loading, onPage, filtered,
  onOpen, onDelete, opening, deleting, busy, batch, onBatchModeChange }) {
  const start = total ? page * pageSize + 1 : 0;
  const end = Math.min(total, (page + 1) * pageSize);
  return <section className="panel" aria-label="「有交换」会话列表">
    <div className="panel-head"><span>有交换会话</span><span className="range">{total ? `${start}–${end} / ${total}` : "暂无记录"}</span></div>
    <div className="monitoring-batch-toolbar"><button type="button" className="toolbar-button"
      disabled={loading || Boolean(batch.busy) || Boolean(busy || opening || deleting)}
      onClick={() => onBatchModeChange(!batch.batchMode)}>
      {batch.batchMode ? "退出批量操作" : "批量操作"}
    </button></div>
    {loading && !conversations.length ? <div className="empty">正在加载有交换会话…</div>
      : !loading && !conversations.length ? <div className="empty">{filtered ? "没有符合筛选条件的会话" : "暂无有交换会话"}</div>
        : <div className="table-scroll"><table className={`monitoring-table monitoring-exchange-table${batch.batchMode ? " monitoring-batch-table" : ""}`}>
          <thead><tr>{batch.batchMode && <th className="select-cell"><input type="checkbox"
            aria-label="全选本页有交换会话" checked={batch.allSelected}
            disabled={loading || Boolean(batch.busy) || !conversations.length}
            onChange={(event) => batch.selectAll(event.target.checked)} /></th>}
            <th>公司 / HR / 岗位</th><th>岗位池</th><th>附件简历</th><th>更新时间<small>入库时间 / 最近扫描时间</small></th><th>操作</th></tr></thead>
          <tbody>{conversations.map((item) => <tr key={monitoringKey(item)}>
            {batch.batchMode && <td className="select-cell"><input type="checkbox"
              aria-label={`选择有交换会话 ${item.company || item.recruiter || item.conversation_id}`}
              checked={batch.selectedKeys.has(monitoringKey(item))}
              disabled={loading || Boolean(batch.busy)} onChange={() => batch.toggle(item)} /></td>}
            <td className="position-cell"><div className="job-title">{item.company || "未知公司"}</div>
              <div className="company">{item.recruiter || "未知 HR"} · {item.job_title || "岗位未知"}</div>
              <div className="job-id">{item.platform} · {item.conversation_id}</div></td>
            <td>{item.in_job_pool ? "池内" : "池外"}</td>
            <td>{item.resume_delivery_message_id
              ? <><span>已确认发送</span><div className="company">回执时间：{formatTime(item.resume_delivered_at)}</div></>
              : "未确认发送"}</td>
            <td className="date-cell">
              <div className="time-block"><span>入库时间</span><time dateTime={item.first_seen_at || undefined}>{formatTime(item.first_seen_at)}</time></div>
              <div className="time-block"><span>更新时间</span><time dateTime={item.last_scanned_at || undefined}>{formatTime(item.last_scanned_at)}</time></div>
            </td>
            <td><div className="filter-actions">
              <button className="text-button" type="button" onClick={() => onOpen(item)}
                disabled={Boolean(busy || opening || deleting || batch.busy)}>
                {opening === `${item.platform}:${item.conversation_id}` ? "打开中…" : "查看会话"}</button>
              <button className="text-button filter-delete-action" type="button" onClick={() => onDelete(item)}
                disabled={Boolean(busy || opening || deleting || batch.busy)}>
                {deleting === `${item.platform}:${item.conversation_id}` ? "删除中…" : "删除"}</button>
            </div></td>
          </tr>)}</tbody>
        </table></div>}
    <div className="pagination"><span>{total ? `显示 ${start}–${end} 条` : "显示 0 条"}</span>
      <div><button type="button" onClick={() => onPage(page - 1)} disabled={loading || Boolean(batch.busy) || page === 0}>上一页</button>
        <span>第 {page + 1} 页</span>
        <button type="button" onClick={() => onPage(page + 1)} disabled={loading || Boolean(batch.busy) || end >= total}>下一页</button></div>
    </div>
    {batch.batchMode && <MonitoringBulkActions label="有交换会话" count={batch.selectedItems.length} batch={batch}
      actions={[{ key: "delete", label: "批量删除", danger: true }]} onClose={batch.close} />}
  </section>;
}
