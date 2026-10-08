const STATUS_LABELS = {
  idle: "尚未开始", running: "监测中", completed: "监测完成", failed: "监测失败",
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
  const pendingFilter = counts.filter_pending == null ? null : Math.max(0,
    counts.filter_pending - (counts.filter_deleted || 0) - (counts.filter_failed || 0));

  return <section className={`collection-status monitoring-status ${monitoring.status || "idle"}`} aria-label="监测任务状态">
    <div className="collection-status-head">
      <strong>本轮任务</strong>
      <span>{STATUS_LABELS[monitoring.status] || monitoring.status || STATUS_LABELS.idle}</span>
    </div>
    {monitoring.status === "failed" && <p className="monitoring-run-error" role="alert">{monitoring.message}</p>}
    <div className="monitoring-phase-grid">
      <PhaseCard title="已沟通会话" phase={communicated}
        settings={[
          monitoring.followup_days != null ? `最近活动 ${monitoring.followup_days} 天` : "最近活动按当前配置",
          monitoring.message_limit != null ? `每条读取 ${monitoring.message_limit} 条消息` : "消息数按当前配置",
          monitoring.followup_enabled === false ? "追问未启用" : "追问语生成后手动发送",
        ]}
        metrics={[
          ["待监测会话", counts.scanned], ["入库或更新", counts.saved],
          ["未读", counts.unread], ["已读未回", counts.read_no_reply],
          ["追问语待发送", counts.followup_pending], ["退出监测范围", counts.left_scope],
        ]} />
      <PhaseCard title="新招呼会话" phase={newGreetings}
        settings={[monitoring.filter_review_required === false ? "命中排除词后自动删除" : "删除前需审核"]}
        metrics={[
          ["已扫描", counts.new_greetings_scanned], ["命中排除词", counts.filter_matches],
          ["待审核删除", pendingFilter], ["已删除", counts.filter_deleted],
          ["删除失败或未确认", counts.filter_failed], ["候选已失效", counts.filter_stale],
        ]} />
    </div>
  </section>;
}
