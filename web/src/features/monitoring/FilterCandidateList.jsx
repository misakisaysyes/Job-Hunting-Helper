import MonitoringBulkActions from "./MonitoringBulkActions";
import { monitoringKey } from "./useMonitoringBatch";

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

const STATUS = {
  pending_review: "待审核删除", deleting: "删除中", deleted: "已删除",
  skipped: "已保留", stale: "已失效", failed: "删除失败", unknown: "删除结果不明",
};

export default function FilterCandidateList({ candidates, total, page, pageSize, loading,
  onPage, onRefresh, onAction, onOpen, onDelete, openingCandidate, busy, busyDelete, filtered, batch, onBatchModeChange }) {
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min(total, (page + 1) * pageSize);
  return <section className="panel" aria-label="新招呼过滤候选">
    <div className="panel-head"><span>过滤候选</span><span className="range">{total ? `${start}–${end} / ${total}` : "暂无记录"}</span></div>
    <div className="monitoring-batch-toolbar"><button type="button" className="toolbar-button"
      disabled={loading || Boolean(batch.busy) || Boolean(busy)} onClick={onRefresh}>
      {loading ? "刷新中…" : "刷新列表"}
    </button><button type="button" className="toolbar-button"
      disabled={loading || Boolean(batch.busy) || Boolean(busy)}
      onClick={() => onBatchModeChange(!batch.batchMode)}>
      {batch.batchMode ? "退出批量操作" : "批量操作"}
    </button></div>
    {loading && candidates.length === 0 ? <div className="empty">正在加载过滤候选…</div>
      : !loading && candidates.length === 0 ? <div className="empty">{filtered ? "没有符合筛选条件的过滤候选" : "暂无命中公司排除词的新招呼"}</div>
        : <div className="table-scroll"><table className={`monitoring-table monitoring-filter-table${batch.batchMode ? " monitoring-batch-table" : ""}`}>
          <thead><tr>{batch.batchMode && <th className="select-cell"><input type="checkbox" aria-label="全选本页过滤候选"
            checked={batch.allSelected} disabled={loading || Boolean(batch.busy) || !candidates.length}
            onChange={(event) => batch.selectAll(event.target.checked)} /></th>}
            <th>公司 / HR / 岗位</th><th>命中排除词</th><th>状态</th><th>更新时间<small>入库时间 / 更新时间</small></th><th>操作</th></tr></thead>
          <tbody>{candidates.map((candidate) => {
            const key = monitoringKey(candidate);
            return <tr key={key}>
            {batch.batchMode && <td className="select-cell"><input type="checkbox"
              aria-label={`选择过滤候选 ${candidate.company || candidate.conversation_id}`}
              checked={batch.selectedKeys.has(key)} disabled={loading || Boolean(batch.busy)}
              onChange={() => batch.toggle(candidate)} /></td>}
            <td className="position-cell">
              <div className="job-title">{candidate.company}</div>
              <div className="company">{candidate.recruiter || "—"} · {candidate.job_title || "岗位未知"}</div>
              <div className="job-id">{candidate.platform} · {candidate.conversation_id}</div>
            </td>
            <td>{candidate.matched_term}</td>
            <td>{STATUS[candidate.status] || candidate.status}
              {candidate.error && <div className="company">{candidate.error}</div>}</td>
            <td className="date-cell">
              <div className="time-block"><span>入库时间</span><time dateTime={candidate.first_seen_at}>{formatTime(candidate.first_seen_at)}</time></div>
              <div className="time-block"><span>更新时间</span><time dateTime={candidate.updated_at}>{formatTime(candidate.updated_at)}</time></div>
            </td>
            <td><div className="filter-actions">
              {["pending_review", "failed", "unknown"].includes(candidate.status) && <>
                <button className="text-button filter-delete-action" type="button" disabled={Boolean(busy || batch.busy)}
                  onClick={() => onAction(candidate)}>
                  {busy === key && !busyDelete ? "处理中…" : candidate.status === "unknown" ? "核对后重试删除" : candidate.status === "failed" ? "重试删除会话" : "删除会话"}
                </button>
              </>}
              <button className="text-button" type="button" onClick={() => onOpen(candidate)}
                disabled={Boolean(openingCandidate || busy || batch.busy) || candidate.status === "deleted"}>
                {openingCandidate === key ? "打开中…" : "查看会话"}
              </button>
              <button className="text-button filter-delete-action" type="button" disabled={Boolean(busy || batch.busy)}
                onClick={() => onDelete(candidate)}>{busyDelete === key ? "删除中…" : "删除记录"}</button>
            </div></td>
          </tr>;
          })}</tbody>
        </table></div>}
    <div className="pagination"><span>{total ? `显示 ${start}–${end} 条` : "显示 0 条"}</span>
      <div><button type="button" onClick={() => onPage(page - 1)} disabled={loading || Boolean(batch.busy) || page === 0}>上一页</button>
        <span>第 {page + 1} 页</span>
        <button type="button" onClick={() => onPage(page + 1)} disabled={loading || Boolean(batch.busy) || end >= total}>下一页</button></div>
    </div>
    {batch.batchMode && <MonitoringBulkActions label="过滤候选" count={batch.selectedItems.length} batch={batch}
      actions={[{ key: "filter", label: "批量删除会话", danger: true },
        { key: "delete", label: "批量删除记录", danger: true }]} onClose={batch.close} />}
  </section>;
}
