import { BatchTaskMetrics, SCAN_METRIC_GROUPS } from "./MonitoringTaskMetrics";

const STATUS_LABELS = {
  idle: "尚未开始", running: "监测中", completed: "监测完成", failed: "监测失败",
};

const BATCH_STATUS_LABELS = {
  running: "执行中", completed: "已完成", completed_with_shortage: "部分完成",
  failed: "执行失败", interrupted: "已中断", stopped: "已停止",
};

const PHASE_LABELS = {
  pending: "待扫描", scanning: "扫描中", processing: "处理中",
  completed: "已完成", failed: "失败", skipped: "未完成",
};

function PhaseCard({ title, phase, metrics, settings }) {
  return <article className={`monitoring-phase ${phase.status}`} aria-label={`${title}扫描情况`}>
    <div className="monitoring-phase-head">
      <h3>{title}</h3>
      <span className="monitoring-phase-status">{PHASE_LABELS[phase.status] || "待扫描"}</span>
    </div>
    <p className="monitoring-phase-message" role="status">{phase.message}</p>
    <div className="monitoring-phase-settings">{settings.map((setting) => <span key={setting}>{setting}</span>)}</div>
    <div className="monitoring-phase-metrics">
      {metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? "—"}</strong></div>)}
    </div>
  </article>;
}

export default function MonitoringStatus({ monitoring }) {
  const counts = monitoring.counts || {};
  const communicated = monitoring.phases?.communicated || {
    status: "pending", message: "等待扫描 BOSS「仅沟通」会话",
  };
  const newGreetings = monitoring.phases?.new_greetings || {
    status: "pending", message: "等待扫描 BOSS「新招呼」会话",
  };
  return <><section className={`collection-status monitoring-status ${monitoring.status || "idle"}`} aria-label="扫描会话任务状态">
    <div className="collection-status-head">
      <strong>扫描会话</strong>
      <span>{STATUS_LABELS[monitoring.status] || monitoring.status || STATUS_LABELS.idle}</span>
    </div>
    {monitoring.status === "failed" && <p className="monitoring-run-error" role="alert">{monitoring.message}</p>}
    <div className="monitoring-phase-grid">
      <PhaseCard title="仅沟通" phase={communicated}
        settings={[
          monitoring.followup_days != null ? `最近活动 ${monitoring.followup_days} 天` : "最近活动按当前配置",
          monitoring.message_limit != null ? `每条读取 ${monitoring.message_limit} 条消息` : "消息数按当前配置",
          monitoring.followup_enabled === false ? "追问未启用" : "追问语生成后手动发送",
        ]}
        metrics={SCAN_METRIC_GROUPS[0].fields.map(([key, label]) => [label, counts[key]])} />
      <PhaseCard title="新会话" phase={newGreetings}
        settings={[monitoring.filter_review_required === false ? "命中排除词后自动删除" : "删除前需审核"]}
        metrics={SCAN_METRIC_GROUPS[1].fields.map(([key, label]) => [label, counts[key]])} />
    </div>
  </section>
    <div className="monitoring-batch-status-grid">
      {[["followup", "monitoring_followup", "批量追问"], ["delete", "monitoring_delete", "批量删除会话"]].map(([action, type, title]) => {
        const state = monitoring.batches?.[action];
        return <section className="monitoring-batch-status-card" aria-label={`${title}任务状态`} key={action}>
          <div className="monitoring-phase-head"><h3>{title}</h3><span>{BATCH_STATUS_LABELS[state?.status] || "尚未开始"}</span></div>
          <BatchTaskMetrics type={type} counts={state?.counts} status={state?.status} />
          {state?.message && <p className={state.status === "failed" ? "detail-error" : ""} role="status">{state.message}</p>}
        </section>;
      })}
    </div>
  </>;
}
