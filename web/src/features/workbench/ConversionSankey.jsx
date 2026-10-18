import { useEffect, useMemo, useRef, useState } from "react";
import { generateConversionFlow, getSavedConversionFlow, refreshConversionCharts } from "../../api/tasks";
import { getCurrentMonitoring } from "../../api/monitoring";
import { useAlertSound } from "../../hooks/useAlertSound";
import { claimTaskCompletion, createTaskCompletionTracker } from "../../lib/taskCompletion";
import { buildConversionLayout } from "./conversionLayout";
import ConversionTrend from "./ConversionTrend";

const LABELS = {
  "source:greeted": "主动投递 · 池内",
  "source:hr_initiated": "HR 联系 · 池外",
  "source:earlier_or_unknown": "期前或未关联",
  "followup:true": "已追问",
  "delivery:true": "有交换",
  "delivery:false": "未观察到交换",
};
const COLORS = {
  "source:greeted": "#1978e6", "source:hr_initiated": "#6398d2",
  "source:earlier_or_unknown": "#9eacbb", "followup:true": "#438dcf",
  "delivery:true": "#168457", "delivery:false": "#a7b4c2",
};
const NODE_WIDTH = 12;
const RESCAN_STORAGE_KEY = "jhh-conversion-rescan-before-generate";

function savedRescanPreference() {
  try {
    return window.localStorage.getItem(RESCAN_STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

function shanghaiDate(daysAgo = 0) {
  const date = new Date(Date.now() + 8 * 60 * 60 * 1000 - daysAgo * 86400000);
  return date.toISOString().slice(0, 10);
}

function formatGeneratedAt(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function linkPath(link) {
  const x1 = link.from.x + NODE_WIDTH;
  const x2 = link.to.x;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${link.y1} C ${mid} ${link.y1}, ${mid} ${link.y2}, ${x2} ${link.y2}`;
}

function ribbonPath(link) {
  const x1 = link.from.x + NODE_WIDTH;
  const x2 = link.to.x;
  const mid = (x1 + x2) / 2;
  const fromTop = link.y1 - link.width / 2;
  const fromBottom = link.y1 + link.width / 2;
  const toTop = link.y2 - link.width / 2;
  const toBottom = link.y2 + link.width / 2;
  return `M ${x1} ${fromTop} C ${mid} ${fromTop}, ${mid} ${toTop}, ${x2} ${toTop} `
    + `L ${x2} ${toBottom} C ${mid} ${toBottom}, ${mid} ${fromBottom}, ${x1} ${fromBottom} Z`;
}

function ribbonColor(link) {
  if (link.delivered) return "#168457";
  if (link.stage === 1) return "#438dcf";
  if (link.stage === 2) return "#88acd1";
  return link.source === "greeted" ? "#a6bad0" : "#bfccd9";
}

function linkLabel(link, labels) {
  return `${labels[link.from.key]} → ${labels[link.to.key]}`;
}

export default function ConversionSankey() {
  const { playConfirmationAlert } = useAlertSound();
  const completionTracker = useRef(createTaskCompletionTracker());
  const [start, setStart] = useState(() => shanghaiDate(29));
  const [end, setEnd] = useState(() => shanghaiDate());
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refreshRunId, setRefreshRunId] = useState(null);
  const [progress, setProgress] = useState("");
  const [restoring, setRestoring] = useState(true);
  const [rescanBeforeGenerate, setRescanBeforeGenerate] = useState(savedRescanPreference);
  const [chartType, setChartType] = useState("sankey");
  const [hoveredLink, setHoveredLink] = useState(null);
  const [basisExpanded, setBasisExpanded] = useState(false);
  const chartFrameRef = useRef(null);
  const today = shanghaiDate();

  useEffect(() => {
    let active = true;
    async function restore() {
      try {
        const { snapshot } = await getSavedConversionFlow();
        if (active && snapshot) {
          setData(snapshot);
          setStart(snapshot.start_date);
          setEnd(snapshot.end_date);
        }
      } catch (cause) {
        if (active) setError(cause.message || "恢复上次生成的图表失败");
      } finally {
        if (active) setRestoring(false);
      }
      try {
        const state = await getCurrentMonitoring();
        if (!active || state.phase !== "conversion") return;
        if (state.status === "running") {
          completionTracker.current.observe(state);
          setStart(state.counts.chart_start);
          setEnd(state.counts.chart_end);
          setProgress(state.message);
          setLoading(true);
          setRefreshRunId(state.run_id);
        } else if (state.status === "completed") {
          const { snapshot } = await getSavedConversionFlow();
          if (active && snapshot) {
            setData(snapshot);
            setStart(snapshot.start_date);
            setEnd(snapshot.end_date);
          }
        }
      } catch (cause) {
        if (active) setError(cause.message || "读取图表刷新状态失败");
      }
    }
    void restore();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (refreshRunId == null) return;
    let active = true;
    let timer;
    async function poll() {
      try {
        const state = await getCurrentMonitoring();
        if (!active) return;
        if (state.run_id !== refreshRunId || state.phase !== "conversion") {
          throw new Error("图表刷新任务已中断，请检查监测任务记录");
        }
        if (completionTracker.current.observe(state)
            && await claimTaskCompletion(`monitoring:${state.run_id}`)) playConfirmationAlert();
        if (state.status === "running") {
          setProgress(state.message);
          timer = window.setTimeout(poll, 2000);
          return;
        }
        if (state.status !== "completed") {
          throw new Error(`监测或图表生成失败：${state.message || state.status}`);
        }
        const { snapshot } = await getSavedConversionFlow();
        if (!active) return;
        if (!snapshot || !Array.isArray(snapshot.daily_metrics)
            || snapshot.start_date !== state.counts.chart_start
            || snapshot.end_date !== state.counts.chart_end) {
          throw new Error("监测已完成，但图表数据未更新，请检查任务记录");
        }
        setData(snapshot);
        setStart(snapshot.start_date);
        setEnd(snapshot.end_date);
        setError("");
      } catch (cause) {
        if (active) setError(cause.message || "刷新图表失败");
      }
      if (active) {
        setLoading(false);
        setProgress("");
        setRefreshRunId(null);
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [refreshRunId]);

  const layout = useMemo(() => buildConversionLayout(data?.flows || []), [data]);
  const confirmedDeliveryBasis = data?.basis_version === 5;
  const labels = confirmedDeliveryBasis ? {
    ...LABELS, "delivery:true": "简历投递成功", "delivery:false": "未确认发送",
  } : LABELS;
  const greetedContacts = (data?.flows || []).filter((flow) => flow.source === "greeted")
    .reduce((sum, flow) => sum + flow.count, 0);
  const greetedDelivered = (data?.flows || []).filter((flow) => flow.source === "greeted" && flow.delivered)
    .reduce((sum, flow) => sum + flow.count, 0);
  const conversion = greetedContacts ? Math.round(greetedDelivered / greetedContacts * 100) : 0;
  const missingRange = !start || !end;
  const invalidRange = start && end && start > end;
  const futureRange = end > today;

  function showLinkTooltip(event, link) {
    const frameRect = chartFrameRef.current?.getBoundingClientRect();
    if (!frameRect) return;
    const targetRect = event.currentTarget.getBoundingClientRect();
    const focused = event.type === "focus";
    const clientX = focused ? targetRect.left + targetRect.width / 2 : event.clientX;
    const clientY = focused ? targetRect.top + targetRect.height / 2 : event.clientY;
    const halfWidth = Math.min(130, frameRect.width / 2);
    setHoveredLink({ link,
      x: Math.max(halfWidth, Math.min(clientX - frameRect.left, frameRect.width - halfWidth)),
      y: clientY - frameRect.top });
  }

  async function refresh() {
    if (loading || restoring || missingRange || invalidRange || futureRange) return;
    setHoveredLink(null);
    setLoading(true);
    setProgress(rescanBeforeGenerate
      ? "正在启动「仅沟通」与「有交换」监测…" : "正在根据已有数据生成图表…");
    setError("");
    try {
      if (!rescanBeforeGenerate) {
        const snapshot = await generateConversionFlow(start, end);
        setData(snapshot);
        setLoading(false);
        setProgress("");
        return;
      }
      const state = await refreshConversionCharts(start, end);
      completionTracker.current.observe(state);
      setProgress(state.message);
      setRefreshRunId(state.run_id);
    } catch (cause) {
      setError(cause.message || "生成图表失败");
      setLoading(false);
      setProgress("");
    }
  }

  function updateRescanPreference(event) {
    const enabled = event.target.checked;
    setRescanBeforeGenerate(enabled);
    try {
      window.localStorage.setItem(RESCAN_STORAGE_KEY, String(enabled));
    } catch {
      // The switch still works for this page when browser storage is unavailable.
    }
  }

  return <section className="workbench-section conversion-section" aria-labelledby="conversion-title">
    <div className="workbench-history-heading">
      <div className="workbench-section-heading">
        <h2 id="conversion-title">招呼到简历投递</h2>
        <p>按所选北京时间日期生成桑基图与折线图，可选择是否先重新扫描「仅沟通」和「有交换」。</p>
      </div>
      <div className="workbench-history-controls conversion-date-controls">
        <label>开始日期<input type="date" value={start} max={end || today} disabled={restoring || loading}
          onChange={(event) => { setStart(event.target.value); setError(""); }} /></label>
        <label>结束日期<input type="date" value={end} min={start} max={today} disabled={restoring || loading}
          onChange={(event) => { setEnd(event.target.value); setError(""); }} /></label>
        <label className="conversion-rescan-toggle" title="开启后先扫描「仅沟通」和「有交换」，关闭后直接使用已有数据">
          <span>生成前重新扫描</span>
          <input type="checkbox" role="switch" checked={rescanBeforeGenerate}
            disabled={restoring || loading} onChange={updateRescanPreference} />
        </label>
        <button type="button" className="conversion-generate-button" onClick={refresh}
          disabled={loading || restoring || missingRange || invalidRange || futureRange}>
          {loading ? rescanBeforeGenerate ? "扫描生成中…" : "生成中…"
            : rescanBeforeGenerate ? "扫描并生成图表" : "生成图表"}
        </button>
      </div>
    </div>
    <div className="conversion-card">
      {missingRange || invalidRange || futureRange ? <p className="conversion-message" role="alert">
        {missingRange ? "请选择开始日期和结束日期。" : invalidRange
          ? "结束日期不能早于开始日期。" : "结束日期不能晚于今天。"}</p> : null}
      {error && <p className="conversion-message error" role="alert">{error}</p>}
      {loading && <p className="conversion-message" role="status">{progress}</p>}
      {data && <><div className="conversion-editorial-head">
        <div>
          <h3>{chartType === "trend" ? "每日沟通趋势" : "沟通转化路径"}</h3>
          <p>{chartType === "trend" ? "按日期查看四类沟通记录的每日数量。" : "从沟通来源，到追问，再到附件简历的发送结果。"}</p>
        </div>
        <div className="conversion-total"><span>{chartType === "trend" ? "统计天数" : "沟通记录"}</span>
          <strong>{chartType === "trend" ? data.daily_metrics?.length ?? "—" : layout.total}</strong></div>
      </div>
      <div className="conversion-chart-meta">
        <strong>{data.start_date} — {data.end_date}</strong>
        <span>生成于 {formatGeneratedAt(data.generated_at)}（北京时间）</span>
      </div></>}
      <div className="conversion-view-tabs" role="group" aria-label="选择图表类型">
        <button type="button" className={chartType === "sankey" ? "active" : ""}
          aria-pressed={chartType === "sankey"}
          onClick={() => { setHoveredLink(null); setChartType("sankey"); }}>桑基图</button>
        <button type="button" className={chartType === "trend" ? "active" : ""}
          aria-pressed={chartType === "trend"}
          onClick={() => { setHoveredLink(null); setChartType("trend"); }}>折线图</button>
      </div>
      {data ? <><div className="conversion-metrics">
        <div><span>主动投递 · 池内</span><strong>{data?.metrics?.greeted ?? "—"}</strong></div>
        <div><span>HR 联系 · 池外</span><strong>{data?.metrics?.hr_initiated ?? "—"}</strong></div>
        <div><span>已追问</span><strong>{data?.metrics?.followups ?? "—"}</strong></div>
        <div><span>{confirmedDeliveryBasis ? "简历投递成功" : "有交换（投递线索）"}</span>
          <strong>{data?.metrics?.deliveries ?? "—"}</strong></div>
        <div className="conversion-rate"><span>{confirmedDeliveryBasis ? "已招呼转投递" : "已招呼转有交换"}</span>
          <strong>{greetedContacts ? `${conversion}%` : "—"}</strong></div>
      </div>
      {chartType === "trend"
        ? !Array.isArray(data.daily_metrics) || !data.daily_metrics.length
          ? <div className="conversion-message">现有快照没有每日数据，请重新生成图表更新统计。</div>
          : <ConversionTrend dailyMetrics={data.daily_metrics} />
        : layout.total === 0 ? <div className="conversion-message">该统计时间段内暂无招呼、追问或有交换记录。</div>
          : <div className="conversion-chart-frame" ref={chartFrameRef}>
            <div className="conversion-chart-scroll"><svg className="conversion-chart" viewBox="0 0 1100 430"
            role="img" aria-label={`${data.start_date} 至 ${data.end_date} 的转化桑基图，共 ${layout.total} 条沟通记录，已招呼 ${data.metrics.greeted} 个岗位，已追问 ${data.metrics.followups} 条会话，${confirmedDeliveryBasis ? "附件简历已发送" : "有交换"} ${data.metrics.deliveries} 条会话`}>
            <line className="conversion-rule" x1="42" x2="1058" y1="65" y2="65" />
            <line className="conversion-rule" x1="42" x2="1058" y1="394" y2="394" />
            <text className="conversion-column-number" x="42" y="46">01</text>
            <text className="conversion-column-title" x="77" y="46">沟通来源</text>
            <text className="conversion-column-number" x="414" y="46">02</text>
            <text className="conversion-column-title" x="449" y="46">追问</text>
            <text className="conversion-column-number" x="765" y="46">03</text>
            <text className="conversion-column-title" x="800" y="46">{confirmedDeliveryBasis ? "简历发送" : "有交换"}</text>
            {layout.links.map((link) => <path key={link.key} className="conversion-ribbon"
              d={ribbonPath(link)} fill={ribbonColor(link)}
              fillOpacity={hoveredLink ? hoveredLink.link.key === link.key ? 0.9 : 0.11
                : link.delivered ? 0.75 : link.stage === 3 ? 0.57 : 0.65}
              pointerEvents="none" />)}
            {[...layout.links].sort((a, b) => b.width - a.width).map((link) => <path
              key={`hit:${link.key}`} d={linkPath(link)} fill="none" stroke="transparent"
              strokeWidth={Math.max(link.width, 12)} pointerEvents="stroke"
              tabIndex="0"
              aria-label={`${linkLabel(link, labels)}${link.stage === 3 ? "，未追问直接到达" : ""}，${link.count} 条沟通记录`}
              onPointerEnter={(event) => showLinkTooltip(event, link)}
              onPointerMove={(event) => showLinkTooltip(event, link)}
              onPointerLeave={() => setHoveredLink(null)}
              onFocus={(event) => showLinkTooltip(event, link)}
              onBlur={() => setHoveredLink(null)} />)}
            {layout.nodes.map((node) => <g key={node.key}>
              <rect x={node.x} y={node.y} width={NODE_WIDTH} height={node.height} rx="2" fill={COLORS[node.key]} />
              <title>{`${labels[node.key]}：${node.count}`}</title>
              <text className="conversion-node-label" x={node.column === 0 ? node.x - 13 : node.x + 25}
                y={node.y + node.height / 2 - 4} dominantBaseline="middle"
                textAnchor={node.column === 0 ? "end" : "start"}>{labels[node.key]}</text>
              <text className="conversion-node-value" x={node.column === 0 ? node.x - 13 : node.x + 25}
                y={node.y + node.height / 2 + 14} dominantBaseline="middle"
                textAnchor={node.column === 0 ? "end" : "start"}>{node.count}</text>
            </g>)}
            <text className="conversion-figure-caption" x="42" y="416">线宽代表沟通记录数（按岗位或会话去重） · 未追问则直达右侧结果</text>
            <text className="conversion-figure-caption" x="1058" y="416" textAnchor="end">HOVER TO TRACE</text>
          </svg></div>
          <p className="conversion-mobile-hint">左右滑动查看完整路径</p>
          {hoveredLink && <div className="conversion-link-tooltip" role="tooltip"
            style={{ left: hoveredLink.x, top: hoveredLink.y,
              transform: hoveredLink.y < 100 ? "translate(-50%, 14px)" : "translate(-50%, calc(-100% - 14px))" }}>
            <div className="conversion-link-tooltip-route">{linkLabel(hoveredLink.link, labels)}</div>
            {hoveredLink.link.stage === 3 && <div className="conversion-link-tooltip-kind">未追问，直接到达</div>}
            <div className="conversion-link-tooltip-value"><strong>{hoveredLink.link.count}</strong> 条沟通记录</div>
            <div className="conversion-link-tooltip-share">占起点 {hoveredLink.link.from.count
              ? `${(hoveredLink.link.count / hoveredLink.link.from.count * 100).toFixed(1)}%` : "—"}</div>
          </div>}</div>}
      <div className="conversion-basis">
        <button type="button" className="conversion-basis-toggle" aria-expanded={basisExpanded}
          aria-controls="conversion-basis-content" onClick={() => setBasisExpanded(!basisExpanded)}>
          {basisExpanded ? "收起统计口径" : "展开统计口径"}<span aria-hidden="true">{basisExpanded ? "⌃" : "⌄"}</span>
        </button>
        <div id="conversion-basis-content" className="conversion-note" hidden={!basisExpanded}>
          {confirmedDeliveryBasis ? <>
            <p>主动投递 · 池内：状态为“已招呼”且更新时间在所选日期内的岗位；不表示简历已发送。</p>
            <p>HR 联系 · 池外：「有交换」中的池外会话，且入库时间在所选日期内。</p>
            <p>{data.followup_basis === "confirmed_send_events"
              ? "已追问：按追问确认发送的北京时间统计，所选日期内同一会话只计一次；折线图归入该范围内首次追问的日期，再次扫描不改变历史统计。"
              : "已追问：这张图按会话更新时间统计。重新生成后，将按追问确认发送时间统计。"}</p>
            <p>简历投递成功：「有交换」会话中已核实附件简历发送回执，或收到“对方已查看了您的附件简历”回执，且入库时间在所选日期内。</p>
            <p>未确认发送：未核实到附件简历发送的其余沟通记录；包含尚未投递或结果未确认，并非已确认发送失败数。</p>
            <p>沟通记录按岗位或会话去重；旧会话需重新扫描「有交换」才能核实附件简历发送。</p>
          </> : <p>{data.basis_version === 4
            ? "这张图按旧口径把「有交换」当作投递线索，不代表已确认投递。重新扫描「有交换」后，重新生成图表即可使用附件简历发送确认口径。"
            : "这张图按旧统计口径生成。重新生成图表后，将按当前岗位与会话状态统计。"}</p>}
        </div>
      </div></>
        : <p className="conversion-message">{restoring ? "正在恢复上次生成的图表…" : "选择日期后生成图表，查看该时间段的统计。"}</p>}
    </div>
  </section>;
}
