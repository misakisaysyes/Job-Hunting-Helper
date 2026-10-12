import { SCAN_METRIC_GROUPS } from "./MonitoringTaskMetrics";

const STATUS_LABELS = {
  idle: "尚未开始", running: "监测中", completed: "监测完成", failed: "监测失败",
};

const PHASE_LABELS = {
  pending: "待扫描", scanning: "扫描中", processing: "处理中",
  completed: "已完成", failed: "失败", skipped: "未完成", not_run: "本轮未启动",
};

function PhaseCard({ title, phaseKey, phase, metrics, settings, onStart,
  starting, statusReady, monitoringBusy }) {
  return <article className={`monitoring-phase ${phase.status}`} aria-label={`${title}扫描情况`}>
    <div className="monitoring-phase-head">
      <h3>{title}</h3>
      <div className="monitoring-phase-actions">
        <span className="monitoring-phase-status">{PHASE_LABELS[phase.status] || "待扫描"}</span>
        <button type="button" className="start-button" onClick={() => onStart(phaseKey)}
          disabled={!statusReady || Boolean(starting) || monitoringBusy}
          aria-label={`启动「${title}」监测`}>
          {!statusReady ? "准备中…" : starting === phaseKey ? "启动中…"
            : monitoringBusy && ["scanning", "processing"].includes(phase.status) ? "进行中…" : `扫描${title}`}
        </button>
      </div>
    </div>
    <p className="monitoring-phase-message" role="status">{phase.message}</p>
    <div className="monitoring-phase-settings">{settings.map((setting) => <span key={setting}>{setting}</span>)}</div>
    <div className="monitoring-phase-metrics">
      {metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? "—"}</strong></div>)}
    </div>
  </article>;
}

export default function MonitoringStatus({ monitoring, onStart, starting, statusReady, monitoringBusy }) {
  const counts = monitoring.counts || {};
  const communicated = monitoring.phases?.communicated || {
    status: "pending", message: "等待扫描 BOSS「仅沟通」会话",
  };
  const exchanged = monitoring.phases?.exchanged || {
    status: "pending", message: "等待扫描 BOSS「有交换」会话",
  };
  const newGreetings = monitoring.phases?.new_greetings || {
    status: "pending", message: "等待扫描 BOSS「新招呼」会话",
  };
  return <section className={`collection-status monitoring-status ${monitoring.status || "idle"}`} aria-label="扫描会话任务状态">
    <div className="collection-status-head">
      <strong>扫描会话</strong>
      <span>{STATUS_LABELS[monitoring.status] || monitoring.status || STATUS_LABELS.idle}</span>
    </div>
    {monitoring.status === "failed" && <p className="monitoring-run-error" role="alert">{monitoring.message}</p>}
    <div className="monitoring-phase-grid">
      <PhaseCard title="仅沟通" phaseKey="communicated" phase={communicated}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.followup_days != null ? `最近活动 ${monitoring.followup_days} 天` : "最近活动按当前配置",
          monitoring.followup_valid_days != null ? `入库后 ${monitoring.followup_valid_days} 天内可追问` : "追问有效期按当前配置",
          monitoring.message_limit != null ? `每条读取 ${monitoring.message_limit} 条消息` : "消息数按当前配置",
          monitoring.followup_enabled === false ? "追问未启用" : "追问语生成后手动发送",
        ]}
        metrics={SCAN_METRIC_GROUPS[0].fields.map(([key, label]) => [label, communicated.status === "not_run" ? null : counts[key]])} />
      <PhaseCard title="有交换" phaseKey="exchanged" phase={exchanged}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.exchanged_active_days != null ? `最近活动 ${monitoring.exchanged_active_days} 天` : "最近活动按当前配置",
          "从「更多 → 有交换」读取会话", "仅记录列表信息",
        ]}
        metrics={SCAN_METRIC_GROUPS[1].fields.map(([key, label]) => [label, exchanged.status === "not_run" ? null : counts[key]])} />
      <PhaseCard title="新会话" phaseKey="new_greetings" phase={newGreetings}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.new_greetings_active_days != null ? `最近活动 ${monitoring.new_greetings_active_days} 天` : "最近活动按当前配置",
          monitoring.filter_review_required === false ? "命中排除词后自动删除" : "删除前需审核",
        ]}
        metrics={SCAN_METRIC_GROUPS[2].fields.map(([key, label]) => [label, newGreetings.status === "not_run" ? null : counts[key]])} />
    </div>
  </section>;
}
