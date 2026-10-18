import { useEffect, useState } from "react";
import { getBasicSettings, getFullSettings } from "../../api/settings";
import { getTodayTasks } from "../../api/tasks";
import JobList from "../jobs/JobList";

const RUN_LABELS = {
  running: "执行中",
  completed: "已完成",
  completed_with_shortage: "部分完成",
  failed: "执行失败",
  stopped: "已停止",
  interrupted: "已中断",
};

const METRICS = [
  ["seen", "发现岗位"], ["duplicate_jobs", "重复岗位"], ["new", "新入库"],
  ["filtered", "预筛排除"], ["ai_scored", "AI 已评分"],
  ["qualified", "达到采集条件"], ["ai_score_failed", "评分失败"],
];

function formatTime(value, showDate = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
    ...(showDate ? { year: "numeric", month: "2-digit", day: "2-digit" } : {}),
  }).format(date);
}

function beijingDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const item = (type) => parts.find((part) => part.type === type)?.value;
  return `${item("year")}-${item("month")}-${item("day")}`;
}

function configSummary(config) {
  if (!config) return "配置暂不可用";
  const recommendSource = `推荐流（${config.expect_id_prefixes?.slice(0, 4).join("、") || "未配置期望"}${config.expect_id_prefixes?.length > 4 ? "等" : ""}）`;
  const searchSource = `搜索流（${[config.cities?.join("、") || config.city, `${config.keywords?.length ?? 0} 个关键词`].filter(Boolean).join("，")}）`;
  const source = config.mode === "both"
    ? `${recommendSource} + ${searchSource}`
    : config.mode === "search"
    ? searchSource
    : config.mode === "recommend"
      ? recommendSource
      : null;
  const salary = config.salary_min || config.salary_max
    ? `月薪 ${config.salary_min || 0}–${config.salary_max || "不限"}K` : null;
  return [source, config.target_jobs == null ? null : `目标 ${config.target_jobs} 个`, salary,
    config.use_ai_score == null ? null : config.use_ai_score
      ? `AI ≥${config.score_threshold ?? "未设置"} 分` : "AI 评分关闭",
    config.silent_background == null ? null : config.silent_background
      ? "静默后台" : "普通浏览器"].filter(Boolean).join(" · ") || "配置暂不可用";
}

function basicConfigSummary(result) {
  const settings = result?.settings;
  if (!settings?.collection) throw new Error("配置接口未返回采集设置");
  return {
    mode: settings.collection.mode,
    expect_id_prefixes: settings.collection.encrypt_expect_id?.map((id) => id.slice(0, 6)),
    keywords: settings.collection.keywords,
    target_jobs: result.preview?.collection?.target_jobs,
    use_ai_score: settings.ai?.use_ai_score,
    score_threshold: result.preview?.ai?.score_threshold,
    silent_background: settings.browser?.silent_background,
  };
}

function fullConfigSummary(settings) {
  const collection = settings.collection;
  const profile = settings.profile;
  return {
    mode: collection.mode,
    expect_id_prefixes: collection.encrypt_expect_id?.map((id) => id.slice(0, 6)),
    keywords: collection.keywords,
    cities: collection.cities,
    city: collection.city,
    target_jobs: collection.target_jobs,
    salary_min: profile?.salary_min,
    salary_max: profile?.salary_max,
    use_ai_score: settings.ai?.use_ai_score,
    score_threshold: settings.ai?.score_threshold,
    silent_background: settings.browser?.silent_background,
  };
}

function metricItems(counts, daily = false) {
  const metrics = METRICS.filter(([key]) => !(daily && key === "duplicate_jobs") &&
    (key !== "ai_scored" || Object.prototype.hasOwnProperty.call(counts || {}, key)))
    .map(([key, label]) => {
      if (key === "duplicate_jobs" && counts?.duplicate_jobs == null && counts?.duplicate != null) {
        return { key, label: "重复（旧口径）", value: counts.duplicate };
      }
      return { key, label, value: counts?.[key] ?? 0 };
    });
  if (daily) metrics.push({ key: "greeted", label: "已招呼", value: counts?.greeted ?? 0 });
  return metrics;
}

function CollectionStatus({ collection }) {
  const [today, setToday] = useState(null);
  const [todayError, setTodayError] = useState("");
  const [fallbackConfig, setFallbackConfig] = useState(null);
  const [configError, setConfigError] = useState("");

  useEffect(() => {
    if (collection.config_summary) return undefined;
    let active = true;
    async function refreshConfig() {
      try {
        const basic = await getBasicSettings();
        if (!active) return;
        setFallbackConfig(basicConfigSummary(basic));
        setConfigError("");
        const full = await getFullSettings().catch(() => null);
        if (active && full && !full.pending_apply && full.settings?.collection) {
          setFallbackConfig(fullConfigSummary(full.settings));
        }
      } catch (cause) {
        if (active) setConfigError(cause.message || "无法读取当前配置");
      }
    }
    refreshConfig();
    return () => { active = false; };
  }, [collection.config_summary]);

  useEffect(() => {
    let active = true;
    let timer;
    async function refresh() {
      try {
        const result = await getTodayTasks();
        if (active) {
          setToday(result);
          setTodayError("");
        }
      } catch (cause) {
        if (active) {
          setToday(null);
          setTodayError(cause.message || "无法读取今日采集记录");
        }
      } finally {
        if (active) timer = window.setTimeout(refresh, 3000);
      }
    }
    refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, []);

  const bucket = today?.collection;
  const recorded = bucket?.history?.[0] || collection.latest_run;
  const recordedId = recorded?.id ?? recorded?.run_id ?? -1;
  const live = collection.run_id && collection.run_id >= recordedId && collection.status !== "idle";
  const latest = live ? collection : recorded;
  const latestId = latest?.run_id ?? latest?.id;
  const isToday = Boolean(today?.date && beijingDate(latest?.started_at) === today.date);
  const inHistory = bucket?.history?.some((run) => run.id === latestId);
  const todayRunNumber = isToday ? (bucket?.runs ?? 0) + (inHistory ? 0 : 1) : null;
  const dailyRuns = isToday && !inHistory ? todayRunNumber : bucket?.runs;
  const dailyCounts = { ...bucket?.totals };
  if (isToday && live) {
    const saved = bucket?.history?.find((run) => run.id === collection.run_id)?.counts || {};
    for (const [key] of METRICS) {
      dailyCounts[key] = (dailyCounts[key] ?? 0)
        + (collection.counts?.[key] ?? 0) - (saved[key] ?? 0);
    }
  }
  const latestStatus = collection.stopping && live ? "正在中断"
    : RUN_LABELS[latest?.status] || latest?.status;
  const statusNote = latest && latest.status !== "completed" && latest.message
    ? latest.message.slice(0, 60) + (latest.message.length > 60 ? "…" : "") : "";
  const latestSummary = latest
    ? `${formatTime(latest.started_at, !isToday)} 开始 · ${latest.finished_at ? `${formatTime(latest.finished_at)} 结束` : "尚未结束"}`
    : "尚无采集记录";
  const latestMetrics = latest && Object.keys(latest.counts || {}).length
    ? metricItems(latest.counts) : [];
  const showDailyMetrics = bucket && (dailyRuns || dailyCounts.greeted);
  const dailyStatus = todayError ? `统计读取失败：${todayError}`
    : !bucket ? "正在读取今日统计…" : showDailyMetrics ? "" : "今日尚未采集";
  const currentConfig = collection.config_summary || fallbackConfig;
  const currentConfigText = currentConfig ? configSummary(currentConfig)
    : configError ? `配置读取失败：${configError}` : "正在读取配置…";

  return <section className="collection-overview" aria-label="采集任务状态">
    <div className="collection-overview-row" role="status">
      <strong>最新执行记录</strong><span title={latest?.message || ""}>{latestSummary}
        {latestStatus && <> · <b className={`collection-overview-run-status ${latest?.status || ""}`}>{latestStatus}</b></>}
        {statusNote && <> · {statusNote}</>}
      </span>
    </div>
    <div className="collection-overview-row">
      <strong>当前配置</strong><span>{currentConfigText}</span>
    </div>
    {latestMetrics.length > 0 && <div className="collection-overview-row">
      <strong>本轮结果</strong><div className="collection-overview-metrics">
        {latestMetrics.map(({ key, label, value }) => <span key={key}>{label} <strong>{value}</strong></span>)}
      </div>
    </div>}
    <div className="collection-overview-row collection-overview-daily" title="按北京时间的开始日期统计；采集数值按各轮相加，重复采集可能重复计入；已招呼按确认发送时间统计">
      <strong>今日累计结果</strong>{dailyStatus ? <span>{dailyStatus}</span>
        : <div className="collection-overview-metrics">
          <span>今日执行 <strong>{dailyRuns ?? 0}</strong> 次</span>
          {metricItems(dailyCounts, true).map(({ key, label, value }) =>
            <span key={key}>{label} <strong>{value}</strong></span>)}
        </div>}
    </div>
  </section>;
}

export default function CollectionPage({
  jobs, total, statusCounts, page, pageSize, loading, scoreThreshold,
  expanded, onExpanded, deleting, onRemove, onJobChange, onPage,
  query, onQueryChange, statusFilter, onStatusFilterChange,
  collection, collectionError, error, thresholdWarning,
  statusReady, starting, stopping, onStart, onStop,
}) {
  return (
    <main className="content collection-page">
      <div className="heading">
        <div>
          <h1>采集任务</h1>
          <p className="subtitle">启动岗位采集，查看任务进度与已入库岗位。</p>
        </div>
        <div className="heading-actions">
          <button className="start-button" onClick={onStart}
            disabled={!statusReady || starting || collection.status === "running"}>
            {!statusReady ? "准备中…" : starting ? "启动中…" : collection.status === "running" ? "正在采集…" : "开始采集"}
          </button>
          {collection.status === "running" && (
            <button type="button" className="stop-button" onClick={onStop}
              disabled={stopping || collection.stopping}>
              {stopping || collection.stopping ? "正在中断…" : "中断采集"}
            </button>
          )}
        </div>
      </div>

      <CollectionStatus collection={collection} />

      {collectionError && <div className="error" role="alert">{collectionError}</div>}
      {error && <div className="error" role="alert">{error}</div>}
      {thresholdWarning && <div className="notice" role="status">{thresholdWarning}</div>}

      <div className="section-heading">
        <h2>已入库岗位</h2>
        <p>岗位状态、评分与详情集中在这里查看。</p>
      </div>
      <JobList jobs={jobs} total={total} statusCounts={statusCounts}
        page={page} pageSize={pageSize} loading={loading}
        scoreThreshold={scoreThreshold} expanded={expanded} onExpanded={onExpanded}
        deleting={deleting} onRemove={onRemove} onJobChange={onJobChange} onPage={onPage}
        query={query} onQueryChange={onQueryChange} statusFilter={statusFilter}
        onStatusFilterChange={onStatusFilterChange} />
    </main>
  );
}
