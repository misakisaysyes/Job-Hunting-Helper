export const SCAN_METRIC_GROUPS = [
  { title: "仅沟通", fields: [["scanned", "扫描会话数"], ["communicated_new", "新入库数"],
    ["read_no_reply", "已读数"], ["unread", "未读数"]] },
  { title: "新会话", fields: [["new_greetings_scanned", "扫描会话数"], ["filter_new", "新入库数（待过滤数）"]] },
];

export function ScanTaskMetrics({ counts, empty = false }) {
  return <div className="monitoring-task-metric-groups">
    {SCAN_METRIC_GROUPS.map(({ title, fields }) => <div key={title}>
      <h5>{title}</h5>
      <div className="workbench-run-metrics">
        {fields.map(([key, label]) => <span key={key}>{label} <strong>{counts?.[key] ?? (empty ? 0 : "—")}</strong></span>)}
      </div>
    </div>)}
  </div>;
}

export function BatchTaskMetrics({ type, counts, status }) {
  const running = status === "running";
  const total = running ? counts?.total ?? 0 : counts?.completed ?? 0;
  return <div className="workbench-run-metrics monitoring-batch-task-metrics">
    <span>{type === "monitoring_followup" ? "总追问数" : "总删除数"} <strong>{total}</strong></span>
    {running && <span>当前已完成数 <strong>{counts?.completed ?? 0}</strong></span>}
  </div>;
}
