import JobList from "../jobs/JobList";

const RUN_LABELS = {
  idle: "尚未开始",
  running: "采集中",
  completed: "采集完成",
  completed_with_shortage: "采集结束，部分数据未完成",
  failed: "采集失败",
  stopped: "采集已停止",
};

export default function CollectionPage({
  jobs, total, page, pageSize, loading, scoreThreshold,
  expanded, onExpanded, deleting, onRemove, onJobChange, onPage,
  query, onQueryChange, statusFilter, onStatusFilterChange,
  collection, collectionError, error, thresholdWarning,
  statusReady, starting, onStart, onRefresh,
}) {
  return (
    <main className="content collection-page">
      <div className="heading">
        <div>
          <p className="eyebrow">WORKBENCH</p>
          <h1>采集任务</h1>
          <p className="subtitle">启动岗位采集，查看任务进度与已入库岗位。</p>
        </div>
        <div className="heading-actions">
          <button className="start-button" onClick={onStart}
            disabled={!statusReady || starting || collection.status === "running"}>
            {!statusReady ? "准备中…" : starting ? "启动中…" : collection.status === "running" ? "正在采集…" : "开始采集"}
          </button>
          <button className="refresh-button" onClick={onRefresh} disabled={loading}>
            ↻ <span>刷新列表</span>
          </button>
        </div>
      </div>

      <section className={`collection-status ${collection.status}`} aria-label="采集状态">
        <div className="collection-status-head">
          <strong>本轮任务</strong>
          <span>{RUN_LABELS[collection.status] || collection.status}</span>
        </div>
        <p>{collection.message || "使用 config.py 的当前配置启动采集。"}</p>
        {collection.status !== "idle" && collection.counts && Object.keys(collection.counts).length > 0 && (
          <div className="collection-counts">
            {collection.counts.target != null &&
              <span>目标进度 {collection.counts.qualified ?? 0}/{collection.counts.target}</span>}
            <span>入库 {collection.counts.new ?? 0}</span>
            <span>重复岗位 {collection.counts.duplicate_jobs ?? 0}</span>
            <span>预筛排除 {collection.counts.filtered ?? 0}</span>
            {collection.counts.ai_scored != null && <span>AI 已评分 {collection.counts.ai_scored}</span>}
            {collection.counts.ai_score_failed != null && <span>评分失败 {collection.counts.ai_score_failed}</span>}
            {collection.counts.ai_greeting_generated != null && <span>招呼语已生成 {collection.counts.ai_greeting_generated}</span>}
            {collection.counts.ai_greeting_failed != null && <span>招呼语生成失败 {collection.counts.ai_greeting_failed}</span>}
          </div>
        )}
      </section>

      {collectionError && <div className="error" role="alert">{collectionError}</div>}
      {error && <div className="error" role="alert">{error}</div>}
      {thresholdWarning && <div className="notice" role="status">{thresholdWarning}</div>}

      <div className="section-heading">
        <h2>已入库岗位</h2>
        <p>岗位状态、评分与详情集中在这里查看。</p>
      </div>
      <JobList jobs={jobs} total={total} page={page} pageSize={pageSize} loading={loading}
        scoreThreshold={scoreThreshold} expanded={expanded} onExpanded={onExpanded}
        deleting={deleting} onRemove={onRemove} onJobChange={onJobChange} onPage={onPage}
        query={query} onQueryChange={onQueryChange} statusFilter={statusFilter}
        onStatusFilterChange={onStatusFilterChange} />
    </main>
  );
}
