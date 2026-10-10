import { BatchTaskMetrics, ScanTaskMetrics } from "../monitoring/MonitoringTaskMetrics";

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
};

function formatTime(value, showDate = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
    ...(showDate ? { month: "2-digit", day: "2-digit" } : {}),
  }).format(date);
}

function Metrics({ type, counts, daily = false, status, empty = false }) {
  if (type === "monitoring") return <ScanTaskMetrics counts={counts} empty={empty} />;
  if (type.startsWith("monitoring_")) return <BatchTaskMetrics type={type} counts={counts} status={status} />;
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

function RunEntries({ type, history, historical }) {
  return <div className="workbench-run-history">
    {history.map((run) => <div className="workbench-run-entry" key={run.id}>
      <div className="workbench-run-entry-head">
        <span>{formatTime(run.started_at, historical)} 开始
          {run.finished_at ? ` · ${formatTime(run.finished_at, historical)} 结束` : ""}</span>
        <span className={`workbench-run-status ${run.status}`}>{RUN_LABELS[run.status] || run.status}</span>
      </div>
      <p>{run.message || "—"}</p>
      {Object.keys(run.counts || {}).length > 0 && <Metrics type={type} counts={run.counts} status={run.status} />}
    </div>)}
  </div>;
}

function RunCard({ type, title, bucket, current, active, onOpen, historical = false }) {
  const history = bucket?.history || [];
  const dayLabel = historical ? "当日" : "今日";
  return <article className="workbench-run-card">
    <div className="workbench-run-heading">
      <div>
        <h3>{title}</h3>
        <p>{dayLabel}执行 <strong>{bucket?.runs ?? 0}</strong> 次</p>
      </div>
      {!historical && <span className={`workbench-run-status ${active ? "running" : "idle"}`}>
        {active ? "正在执行" : "当前未运行"}
      </span>}
    </div>
    {active && <p className="workbench-run-active" role="status">
      本轮 {formatTime(current?.started_at)} 开始 · {current?.message || "执行中"}
    </p>}
    <h4>{dayLabel}累计结果</h4>
    <Metrics type={type} counts={bucket?.totals} daily />
    <div className="workbench-run-history-head">
      <h4>执行记录</h4>
      {onOpen && <button type="button" onClick={onOpen}>查看{title}</button>}
    </div>
    {history.length ? <RunEntries type={type} history={history} historical={historical} />
      : <p className="workbench-run-empty">{historical ? "当日没有执行记录。" : "今天还没有执行记录。"}</p>}
  </article>;
}

const MONITORING_TASKS = [
  ["monitoring", "扫描会话"], ["monitoring_followup", "批量追问"], ["monitoring_delete", "批量删除会话"],
];

function MonitoringRunCard({ data, historical, onOpen }) {
  const active = MONITORING_TASKS.some(([type]) => data?.active?.[type]);
  const runs = MONITORING_TASKS.reduce((sum, [type]) => sum + (data?.[type]?.runs || 0), 0);
  return <article className="workbench-run-card">
    <div className="workbench-run-heading">
      <div><h3>监测任务</h3><p>{historical ? "当日" : "今日"}执行 <strong>{runs}</strong> 次</p></div>
      {!historical && <span className={`workbench-run-status ${active ? "running" : "idle"}`}>{active ? "正在执行" : "当前未运行"}</span>}
    </div>
    {MONITORING_TASKS.map(([type, title]) => {
      const bucket = data?.[type];
      const history = bucket?.history || [];
      const running = history.some((run) => run.status === "running");
      const current = data?.current?.[type];
      return <section className="workbench-monitoring-task" key={type} aria-label={title}>
        <div className="workbench-run-history-head"><h4>{title}</h4>
          {type === "monitoring" && onOpen && <button type="button" onClick={onOpen}>查看监测任务</button>}
        </div>
        {data?.active?.[type] && <p className="workbench-run-active" role="status">{current?.message || "执行中"}</p>}
        <Metrics type={type} counts={bucket?.totals} daily empty={!bucket?.runs} status={running ? "running" : "completed"} />
        {history.length ? <details className="workbench-task-records">
          <summary>执行记录（{bucket.runs} 次）</summary>
          <RunEntries type={type} history={history} historical={historical} />
        </details> : <p className="workbench-run-empty">{historical ? "当日没有执行记录。" : "今天还没有执行记录。"}</p>}
      </section>;
    })}
  </article>;
}

export function TaskRunCards({ data, historical = false, onOpenCollection, onOpenMonitoring }) {
  return <div className="workbench-run-grid">
    <RunCard type="collection" title="采集任务" bucket={data?.collection}
      current={data?.current?.collection} active={data?.active?.collection}
      onOpen={onOpenCollection} historical={historical} />
    <MonitoringRunCard data={data} historical={historical} onOpen={onOpenMonitoring} />
  </div>;
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
        ? `正在执行：${[active.collection && "采集任务", active.monitoring && "扫描会话",
          active.monitoring_followup && "批量追问", active.monitoring_delete && "批量删除会话"].filter(Boolean).join("、")}`
        : "当前没有正在执行的任务"}
    </div>
    {error && <p className="workbench-settings-notice error" role="alert">{error}</p>}
    <TaskRunCards data={data} onOpenCollection={onOpenCollection} onOpenMonitoring={onOpenMonitoring} />
  </section>;
}
