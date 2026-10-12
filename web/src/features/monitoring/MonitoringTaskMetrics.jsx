export const SCAN_METRIC_GROUPS = [
  { phase: "communicated", title: "仅沟通", fields: [["scanned", "扫描会话数"], ["communicated_new", "新入库数"],
    ["read_no_reply", "已读数"], ["unread", "未读数"]] },
  { phase: "exchanged", title: "有交换", fields: [["exchanged_scanned", "扫描会话数"], ["exchanged_new", "新入库数"]] },
  { phase: "new_greetings", title: "新会话", fields: [["new_greetings_scanned", "扫描会话数"], ["filter_new", "新入库数（待过滤数）"]] },
];

export function ScanTaskMetrics({ counts, empty = false, phase: selectedPhase, showTitle = true }) {
  return <div className="monitoring-task-metric-groups">
    {SCAN_METRIC_GROUPS.filter(({ phase }) => !(selectedPhase || counts?.phase)
      || phase === (selectedPhase || counts?.phase)).map(({ title, fields }) => <div key={title}>
      {showTitle && <h5>{title}</h5>}
      <div className="workbench-run-metrics">
        {fields.map(([key, label]) => <span key={key}>{label} <strong>{counts?.[key] ?? (empty ? 0 : "—")}</strong></span>)}
      </div>
    </div>)}
  </div>;
}
