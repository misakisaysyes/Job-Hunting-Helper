import { SCAN_METRIC_GROUPS } from "./MonitoringTaskMetrics";

const STATUS_LABELS = {
  idle: "尚未开始", running: "监测中", completed: "监测完成", failed: "监测失败",
};

const PHASE_LABELS = {
  pending: "待扫描", scanning: "扫描中", processing: "处理中",
  completed: "已完成", failed: "失败", skipped: "未完成", not_run: "待扫描",
  interrupted: "已中断", running: "扫描中",
};

const ACTIVE_PHASES = new Set(["pending", "scanning", "processing", "running"]);
const FINISHED_PHASES = new Set(["completed", "failed", "interrupted", "skipped", "stopped"]);

function formatTaskTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function phaseView(monitoring, key, fallback) {
  const run = monitoring.phase_runs?.[key];
  const today = monitoring.today_phase_stats?.[key];
  if (today && (!today.runs || today.latest_run_id !== run?.run_id)) {
    return { phase: { status: "not_run", message: "今日尚未执行" }, counts: {}, previous: null };
  }
  if (!run) {
    const phase = monitoring.phases?.[key] || fallback;
    return { phase, counts: phase.status === "not_run" ? {} : monitoring.counts || {}, previous: null };
  }
  const live = monitoring.status === "running" && monitoring.run_id === run.run_id
    && ["pending", "scanning", "processing", "running"].includes(run.status)
    ? monitoring.phases?.[key] : null;
  const phase = live && live.status !== "not_run" ? { ...run, ...live } : run;
  const result = phase.status === "completed" ? run : run.last_completed || run;
  return { phase, counts: result.counts || {},
    previous: result.run_id !== run.run_id ? result : null };
}

function PhaseCard({ title, phaseKey, phase, metrics, previous, settings, todayStats, onStart,
  starting, statusReady, monitoringBusy }) {
  const finished = FINISHED_PHASES.has(phase.status);
  const showResult = phase.status === "completed" || Boolean(previous);
  const completedWithTime = phase.status === "completed" && phase.started_at && phase.finished_at;
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
    {completedWithTime
      ? <p className="monitoring-phase-times">当天第 {todayStats?.runs ?? 1} 次扫描 · {formatTaskTime(phase.started_at)} 开始 · {formatTaskTime(phase.finished_at)} 结束</p>
      : <p className={`monitoring-phase-message${phase.started_at && ACTIVE_PHASES.has(phase.status) ? " progress" : ""}`}
          role="status">{phase.status === "not_run" || !phase.started_at && phase.status === "pending"
            ? "今日尚未执行" : phase.message}</p>}
    {finished && !completedWithTime && phase.started_at && phase.finished_at &&
      <p className="monitoring-phase-times">当天第 {todayStats?.runs ?? 1} 次扫描 · {formatTaskTime(phase.started_at)} 开始 · {formatTaskTime(phase.finished_at)} 结束</p>}
    <div className="monitoring-phase-settings">{settings.join(" · ")}</div>
    {previous && <p className="monitoring-phase-previous-result">
      显示上次完成结果（{formatTaskTime(previous.finished_at)} 结束）
      {["failed", "interrupted", "skipped"].includes(phase.status)
        ? "；本轮未完成。" : "；本轮数据仍在更新。"}
    </p>}
    {showResult && <div className="monitoring-phase-metrics">
      {metrics.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? "—"}</strong></div>)}
    </div>}
    <p className="monitoring-phase-daily-total" title="按今天各次已完成扫描的会话数相加；重复扫描可能重复计入">
      今日监测总数：<strong>{todayStats?.scanned_total ?? 0}</strong> 条
    </p>
  </article>;
}

export default function MonitoringStatus({ monitoring, onStart, starting, statusReady, monitoringBusy }) {
  const communicated = phaseView(monitoring, "communicated", {
    status: "pending", message: "等待扫描 BOSS「仅沟通」会话",
  });
  const exchanged = phaseView(monitoring, "exchanged", {
    status: "pending", message: "等待扫描 BOSS「有交换」会话",
  });
  const newGreetings = phaseView(monitoring, "new_greetings", {
    status: "pending", message: "等待扫描 BOSS「新招呼」会话",
  });
  const hasTodayRuns = Object.values(monitoring.today_phase_stats || {}).some((item) => item.runs > 0);
  const overallStatus = monitoring.status === "idle" && hasTodayRuns
    ? "今日已有扫描" : STATUS_LABELS[monitoring.status] || monitoring.status || STATUS_LABELS.idle;
  return <section className={`collection-status monitoring-status ${monitoring.status || "idle"}`} aria-label="扫描会话任务状态">
    <div className="collection-status-head">
      <strong>扫描会话</strong>
      <span>{overallStatus}</span>
    </div>
    {monitoring.status === "failed" && <p className="monitoring-run-error" role="alert">{monitoring.message}</p>}
    <div className="monitoring-phase-grid">
      <PhaseCard title="仅沟通" phaseKey="communicated" phase={communicated.phase}
        previous={communicated.previous} todayStats={monitoring.today_phase_stats?.communicated}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.followup_days != null ? `扫描最近 ${monitoring.followup_days} 天会话` : "扫描范围按当前配置",
          monitoring.followup_valid_days != null ? `入库后 ${monitoring.followup_valid_days} 天内可追问` : "追问有效期按当前配置",
          monitoring.message_limit != null ? `每条读取 ${monitoring.message_limit} 条消息` : "消息数按当前配置",
          monitoring.followup_cooldown_hours != null ? `追问冷却 ${monitoring.followup_cooldown_hours} 小时` : "追问冷却按当前配置",
          monitoring.followup_max_count != null ? `最多追问 ${monitoring.followup_max_count} 次` : "追问次数按当前配置",
          monitoring.followup_enabled === false ? "追问未启用" : "追问语生成后手动发送",
        ]}
        metrics={SCAN_METRIC_GROUPS[0].fields.map(([key, label]) => [label, communicated.counts[key]])} />
      <PhaseCard title="有交换" phaseKey="exchanged" phase={exchanged.phase}
        previous={exchanged.previous} todayStats={monitoring.today_phase_stats?.exchanged}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.exchanged_active_days != null ? `扫描最近 ${monitoring.exchanged_active_days} 天会话` : "扫描范围按当前配置",
          "从「更多 → 有交换」读取会话", "仅记录列表信息",
        ]}
        metrics={SCAN_METRIC_GROUPS[1].fields.map(([key, label]) => [label, exchanged.counts[key]])} />
      <PhaseCard title="新会话" phaseKey="new_greetings" phase={newGreetings.phase}
        previous={newGreetings.previous} todayStats={monitoring.today_phase_stats?.new_greetings}
        onStart={onStart} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy}
        settings={[
          monitoring.new_greetings_active_days != null ? `扫描最近 ${monitoring.new_greetings_active_days} 天会话` : "扫描范围按当前配置",
          monitoring.filter_review_required === false ? "命中排除词后自动删除" : "删除前需审核",
        ]}
        metrics={SCAN_METRIC_GROUPS[2].fields.map(([key, label]) => [label, newGreetings.counts[key]])} />
    </div>
  </section>;
}
