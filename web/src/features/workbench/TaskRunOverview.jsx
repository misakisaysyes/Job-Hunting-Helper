const RUN_LABELS = {
  running: "执行中", completed: "已完成", completed_with_shortage: "部分完成",
  failed: "执行失败", stopped: "已停止", interrupted: "已中断",
};

const METRICS = {
  collection: [
    ["seen", "发现岗位"], ["duplicate_jobs", "重复岗位"], ["new", "新入库"],
    ["filtered", "预筛排除"], ["ai_scored", "AI 已评分"],
    ["qualified", "达到采集条件"], ["ai_score_failed", "评分失败"],
  ],
  monitoring: [
    ["scanned", "扫描会话"], ["saved", "入库或更新"],
    ["left_scope", "退出监测范围"], ["unread", "未读"],
    ["read_no_reply", "已读未回"],
  ],
};

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function Metrics({ type, counts, daily = false }) {
  return <div className="workbench-run-metrics">
    {METRICS[type].filter(([key]) => !(daily && type === "collection" && key === "duplicate_jobs") &&
      (key !== "ai_scored" || Object.prototype.hasOwnProperty.call(counts || {}, key)))
      .map(([key, label]) => key === "duplicate_jobs" &&
        !Object.prototype.hasOwnProperty.call(counts || {}, key) &&
        Object.prototype.hasOwnProperty.call(counts || {}, "duplicate")
        ? <span key={key}>重复（旧口径） <strong>{counts.duplicate}</strong></span>
        : <span key={key}>{label} <strong>{counts?.[key] ?? 0}</strong></span>)}
    {daily && type === "collection" && <span>已招呼 <strong>{counts?.greeted ?? 0}</strong></span>}
  </div>;
}

function RunCard({ type, title, bucket, current, active, onOpen }) {
  const history = bucket?.history || [];
  return <article className="workbench-run-card">
    <div className="workbench-run-heading">
      <div>
        <h3>{title}</h3>
        <p>今日执行 <strong>{bucket?.runs ?? 0}</strong> 次</p>
      </div>
      <span className={`workbench-run-status ${active ? "running" : "idle"}`}>
        {active ? "正在执行" : "当前未运行"}
      </span>
    </div>
    {active && <p className="workbench-run-active" role="status">
      本轮 {formatTime(current?.started_at)} 开始 · {current?.message || "执行中"}
    </p>}
    <h4>今日累计结果</h4>
    <Metrics type={type} counts={bucket?.totals} daily />
    <div className="workbench-run-history-head">
      <h4>执行记录</h4>
      <button type="button" onClick={onOpen}>查看{title}</button>
    </div>
    {history.length ? <div className="workbench-run-history">
      {history.map((run) => <div className="workbench-run-entry" key={run.id}>
        <div className="workbench-run-entry-head">
          <span>{formatTime(run.started_at)} 开始 · {formatTime(run.finished_at)} 结束</span>
          <span className={`workbench-run-status ${run.status}`}>
            {RUN_LABELS[run.status] || run.status}
          </span>
        </div>
        <p>{run.message || "—"}</p>
        {Object.keys(run.counts || {}).length > 0 && <Metrics type={type} counts={run.counts} />}
      </div>)}
    </div> : <p className="workbench-run-empty">今天还没有执行记录。</p>}
  </article>;
}

export default function TaskRunOverview({ data, error, onOpenCollection, onOpenMonitoring }) {
  const active = data?.active;
  return <section className="workbench-section" aria-labelledby="task-runs-title">
    <div className="workbench-section-heading">
      <h2 id="task-runs-title">今日任务</h2>
      <p>{data?.date || "今天"} · 任务按开始时间计入当天，已招呼按确认发送时间统计。</p>
    </div>
    <div className={`workbench-overall-status ${active?.any ? "running" : ""}`} role="status">
      {active?.any
        ? `正在执行：${[active.collection && "采集任务", active.monitoring && "监测任务"].filter(Boolean).join("、")}`
        : "当前没有正在执行的任务"}
    </div>
    {error && <p className="workbench-settings-notice error" role="alert">{error}</p>}
    <div className="workbench-run-grid">
      <RunCard type="collection" title="采集任务" bucket={data?.collection}
        current={data?.current?.collection} active={active?.collection} onOpen={onOpenCollection} />
      <RunCard type="monitoring" title="监测任务" bucket={data?.monitoring}
        current={data?.current?.monitoring} active={active?.monitoring} onOpen={onOpenMonitoring} />
    </div>
  </section>;
}
