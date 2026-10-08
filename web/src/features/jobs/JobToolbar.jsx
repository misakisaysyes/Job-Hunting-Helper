import React from "react";

export default function JobToolbar({ query, onQueryChange, status, onStatusChange,
  batchMode, onBatchModeChange, loading, busy }) {
  return <div className="job-toolbar">
    <input type="search" aria-label="关键词筛选" value={query} disabled={busy}
      onChange={(event) => onQueryChange(event.target.value)}
      placeholder="关键词自动匹配岗位名称、公司、地点、JD、薪资、年限、学历，并可以用&&、||、()组合" />
    <select aria-label="岗位状态筛选" value={status} disabled={busy}
      onChange={(event) => onStatusChange(event.target.value)}>
      <option value="">全部状态</option>
      <option value="scored">已评分</option>
      <option value="greeting_ready">打招呼</option>
      <option value="greeted">已招呼</option>
      <option value="ended">已结束</option>
    </select>
    <button type="button" className="toolbar-button" disabled={loading}
      onClick={() => onBatchModeChange(!batchMode)}>
      {batchMode ? "退出批量操作" : "批量操作"}
    </button>
  </div>;
}
