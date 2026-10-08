import React from "react";
import { listStatusInfo } from "../../lib/jobStatus";
import BulkActions from "./BulkActions";
import JobDetails from "./JobDetails";
import JobToolbar from "./JobToolbar";
import useJobBatch, { jobKey } from "./useJobBatch";

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function scoreLabel(job, threshold) {
  if (job.ai_score_status === "scored" && job.ai_score != null) {
    return {
      text: `${job.ai_score} 分`,
      className: Number.isFinite(threshold) && Number(job.ai_score) >= threshold
        ? "score qualified" : "score neutral",
    };
  }
  if (job.ai_score_status === "score_failed") return { text: "评分失败", className: "score neutral" };
  return { text: "未评分", className: "score neutral" };
}

export default function JobList({ jobs, total, page, pageSize, loading, scoreThreshold,
  expanded, onExpanded, deleting, onRemove, onJobChange, onPage,
  query, onQueryChange, statusFilter, onStatusFilterChange }) {
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min(total, (page + 1) * pageSize);
  const batch = useJobBatch({ jobs, total, page, pageSize, query, statusFilter,
    onReload: onPage, onJobChange, loading });
  return (
    <section className="panel" aria-label="岗位列表">
      <div className="panel-head">
        <span>岗位列表</span>
        <span className="range">{total ? `${start}–${end} / ${total}` : "暂无记录"} · 标色门槛 {scoreThreshold ?? "—"} 分</span>
      </div>
      <JobToolbar query={query} onQueryChange={onQueryChange}
        status={statusFilter} onStatusChange={onStatusFilterChange}
        batchMode={batch.batchMode} loading={loading || Boolean(batch.busy)} busy={Boolean(batch.busy)}
        onBatchModeChange={(enabled) => enabled ? batch.setBatchMode(true) : batch.close()} />
      {loading && jobs.length === 0 ? (
        <div className="empty">正在加载岗位…</div>
      ) : !loading && jobs.length === 0 ? (
        <div className="empty"><div className="empty-icon">□</div><strong>{query || statusFilter ? "没有符合条件的岗位" : "暂无岗位数据"}</strong>
          <span>{query || statusFilter ? "调整关键词或岗位状态后重试。" : "采集并通过预筛的岗位会显示在这里。"}</span></div>
      ) : (
        <div className="table-scroll">
          <table className={batch.batchMode ? "batch-table" : ""}>
            <thead><tr>
              {batch.batchMode && <th className="select-cell"><input type="checkbox" aria-label="全选本页岗位"
                checked={batch.allSelected} disabled={loading || Boolean(batch.busy) || !jobs.length}
                onChange={(event) => batch.selectAll(event.target.checked)} /></th>}
              <th>岗位 / 公司</th><th>薪资与要求</th><th>岗位评分</th><th>岗位状态</th><th>变更时间</th><th className="actions-head">操作</th>
            </tr></thead>
            <tbody>{jobs.map((job) => {
              const key = jobKey(job);
              const score = scoreLabel(job, scoreThreshold);
              const status = listStatusInfo(job, scoreThreshold);
              return <React.Fragment key={key}>
                <tr>
                  {batch.batchMode && <td className="select-cell"><input type="checkbox"
                    aria-label={`选择 ${job.title || job.source_job_id}`} checked={batch.selectedKeys.has(key)}
                    disabled={loading || Boolean(batch.busy)} onChange={() => batch.toggle(job)} /></td>}
                  <td className="position-cell"><div className="job-title">{job.title || "未命名岗位"}</div>
                    <div className="company">{job.company || "未知公司"} <span className="dot">·</span> {job.city || "城市未知"}</div>
                    <div className="job-id">{job.source_platform} · {job.source_job_id}</div></td>
                  <td><div className="salary">{job.salary || "薪资未标注"}</div><div className="requirement">{[job.experience, job.education].filter(Boolean).join(" · ") || "要求未标注"}</div></td>
                  <td><span className={score.className}>{score.text}</span></td>
                  <td><button type="button" className={`job-status ${status.className}`} onClick={() => onExpanded(expanded === key ? "" : key)} aria-expanded={expanded === key}>{status.label}</button></td>
                  <td className="date-cell"><div className="time-block"><span>入库时间</span><time dateTime={job.collected_at}>{formatTime(job.collected_at)}</time></div>
                    <div className="time-block"><span>更新时间</span><time dateTime={job.updated_at}>{formatTime(job.updated_at)}</time></div></td>
                  <td className="actions"><button className="text-button" onClick={() => onExpanded(expanded === key ? "" : key)} aria-expanded={expanded === key}>{expanded === key ? "收起" : "详情"}</button>
                    <button className="delete-button" onClick={() => onRemove(job)} disabled={deleting === key || Boolean(batch.busy)}>{deleting === key ? "删除中" : "删除"}</button></td>
                </tr>
                {expanded === key && <tr className="details-row"><td colSpan={batch.batchMode ? 7 : 6}><JobDetails job={job} scoreThreshold={scoreThreshold} onJobChange={onJobChange} /></td></tr>}
              </React.Fragment>;
            })}</tbody>
          </table>
        </div>
      )}
      <div className="pagination"><span>{total ? `显示 ${start}–${end} 条` : "显示 0 条"}</span>
        <div><button onClick={() => onPage(page - 1)} disabled={loading || Boolean(batch.busy) || page === 0}>上一页</button><span>第 {page + 1} 页</span><button onClick={() => onPage(page + 1)} disabled={loading || Boolean(batch.busy) || end >= total}>下一页</button></div>
      </div>
      {batch.batchMode && <BulkActions count={batch.selectedJobs.length} busy={batch.busy || loading}
        progress={batch.progress} notice={batch.notice} onAction={batch.run} onClose={batch.close} />}
    </section>
  );
}
