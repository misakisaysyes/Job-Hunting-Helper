import React from "react";

export default function JobToolbar({ query, onQueryChange, status, onStatusChange,
  statusCounts, batchMode, onBatchModeChange, loading, busy }) {
  const countLabel = (label, key) => statusCounts ? `${label}（${statusCounts[key] ?? 0}）` : label;
  return <div className="job-toolbar">
    <input type="search" aria-label="关键词筛选" value={query} disabled={busy}
      onChange={(event) => onQueryChange(event.target.value)}
      placeholder="关键词自动匹配岗位名称、公司、地点、来源、JD、薪资、年限、学历，并可以用&&、||、()组合" />
    <select aria-label="岗位状态筛选" value={status} disabled={busy}
      onChange={(event) => onStatusChange(event.target.value)}>
      <option value="">{countLabel("全部状态", "all")}</option>
      <option value="scored">{countLabel("已评分", "scored")}</option>
      <option value="greeting_ready">{countLabel("打招呼", "greeting_ready")}</option>
      <option value="greeted">{countLabel("已招呼", "greeted")}</option>
      <option value="ended">{countLabel("已结束", "ended")}</option>
    </select>
    <button type="button" className="toolbar-button" disabled={loading}
      onClick={() => onBatchModeChange(!batchMode)}>
      {batchMode ? "退出批量操作" : "批量操作"}
    </button>
  </div>;
}
