import { useEffect, useMemo, useRef, useState } from "react";
import { generateConversionFlow, getSavedConversionFlow } from "../../api/tasks";
import { buildConversionLayout } from "./conversionLayout";

const LABELS = {
  "source:greeted": "主动投递 · 池内",
  "source:hr_initiated": "HR 联系 · 池外",
  "source:earlier_or_unknown": "期前或未关联",
  "followup:true": "已追问",
  "delivery:true": "有交换",
  "delivery:false": "未观察到交换",
};
const COLORS = {
  "source:greeted": "#3284dc", "source:hr_initiated": "#50b7a3",
  "source:earlier_or_unknown": "#a2aec0", "followup:true": "#8d73d7",
  "delivery:true": "#21a67d",
  "delivery:false": "#c4ceda",
};

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
  const x1 = link.from.x + 16;
  const x2 = link.to.x;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${link.y1} C ${mid} ${link.y1}, ${mid} ${link.y2}, ${x2} ${link.y2}`;
}

function linkLabel(link, labels) {
  return `${labels[link.from.key]} → ${labels[link.to.key]}`;
}

export default function ConversionSankey() {
  const [start, setStart] = useState(() => shanghaiDate(29));
  const [end, setEnd] = useState(() => shanghaiDate());
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [hoveredLink, setHoveredLink] = useState(null);
  const [basisExpanded, setBasisExpanded] = useState(false);
  const chartFrameRef = useRef(null);
  const today = shanghaiDate();

  useEffect(() => {
    let active = true;
    getSavedConversionFlow().then(({ snapshot }) => {
      if (!active || !snapshot) return;
      setData(snapshot);
      setStart(snapshot.start_date);
      setEnd(snapshot.end_date);
    }).catch((cause) => {
      if (active) setError(cause.message || "恢复上次生成的桑基图失败");
    }).finally(() => {
      if (active) setRestoring(false);
    });
    return () => { active = false; };
  }, []);

  const layout = useMemo(() => buildConversionLayout(data?.flows || []), [data]);
  const confirmedDeliveryBasis = data?.basis_version === 5;
  const labels = confirmedDeliveryBasis ? {
    ...LABELS, "delivery:true": "简历投递成功", "delivery:false": "简历投递失败",
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

  async function generate() {
    if (loading || restoring || missingRange || invalidRange || futureRange) return;
    const requestedStart = start;
    const requestedEnd = end;
    setHoveredLink(null);
    setLoading(true);
    setError("");
    try {
      setData(await generateConversionFlow(requestedStart, requestedEnd));
    } catch (cause) {
      setError(cause.message || "生成桑基图失败");
    } finally {
      setLoading(false);
    }
  }

  return <section className="workbench-section conversion-section" aria-labelledby="conversion-title">
    <div className="workbench-history-heading">
      <div className="workbench-section-heading">
        <h2 id="conversion-title">招呼到简历投递</h2>
        <p>按上海时间统计所选日期内的岗位与会话状态，查看招呼到附件简历发送的转化路径。</p>
      </div>
      <div className="workbench-history-controls conversion-date-controls">
        <label>开始日期<input type="date" value={start} max={end || today} disabled={restoring}
          onChange={(event) => { setStart(event.target.value); setError(""); }} /></label>
        <label>结束日期<input type="date" value={end} min={start} max={today} disabled={restoring}
          onChange={(event) => { setEnd(event.target.value); setError(""); }} /></label>
        <button type="button" className="conversion-generate-button" onClick={generate}
          disabled={loading || restoring || missingRange || invalidRange || futureRange}>
          {loading ? "生成中…" : "生成桑基图"}
        </button>
      </div>
    </div>
    <div className="conversion-card">
      {missingRange || invalidRange || futureRange ? <p className="conversion-message" role="alert">
        {missingRange ? "请选择开始日期和结束日期。" : invalidRange
          ? "结束日期不能早于开始日期。" : "结束日期不能晚于今天。"}</p> : null}
      {error && <p className="conversion-message error" role="alert">{error}</p>}
      {data && <div className="conversion-chart-meta">
        <strong>统计时间段：{data.start_date} 至 {data.end_date}</strong>
        <span>生成时间：{formatGeneratedAt(data.generated_at)}（北京时间）</span>
      </div>}
      {data ? <><div className="conversion-metrics">
        <div><span>主动投递 · 池内</span><strong>{data?.metrics?.greeted ?? "—"}</strong></div>
        <div><span>HR 联系 · 池外</span><strong>{data?.metrics?.hr_initiated ?? "—"}</strong></div>
        <div><span>已追问</span><strong>{data?.metrics?.followups ?? "—"}</strong></div>
        <div><span>{confirmedDeliveryBasis ? "简历投递成功" : "有交换（投递线索）"}</span>
          <strong>{data?.metrics?.deliveries ?? "—"}</strong></div>
        <div className="conversion-rate"><span>{confirmedDeliveryBasis ? "已招呼转投递" : "已招呼转有交换"}</span>
          <strong>{greetedContacts ? `${conversion}%` : "—"}</strong></div>
      </div>
      {layout.total === 0 ? <div className="conversion-message">该统计时间段内暂无招呼、追问或有交换记录。</div>
          : <div className="conversion-chart-frame" ref={chartFrameRef}>
            <div className="conversion-chart-scroll"><svg className="conversion-chart" viewBox="0 0 1000 370"
            role="img" aria-label={`${data.start_date} 至 ${data.end_date} 的转化桑基图，共 ${layout.total} 个关联对象，已招呼 ${data.metrics.greeted} 个岗位，已追问 ${data.metrics.followups} 条会话，${confirmedDeliveryBasis ? "附件简历已发送" : "有交换"} ${data.metrics.deliveries} 条会话`}>
            <text className="conversion-column-title" x="142" y="25">来源</text>
            <text className="conversion-column-title" x="500" y="25">追问</text>
            <text className="conversion-column-title" x="856" y="25">{confirmedDeliveryBasis ? "已投递" : "有交换"}</text>
            {layout.links.map((link) => <path key={link.key} d={linkPath(link)} fill="none"
              stroke={link.stage === 2 ? COLORS[`delivery:${link.delivered}`] : COLORS[`source:${link.source}`]}
              strokeOpacity={hoveredLink?.link.key === link.key ? 0.82
                : link.stage === 3 ? link.delivered ? 0.5 : 0.26 : link.stage === 1 ? 0.34 : 0.42}
              strokeWidth={link.width} pointerEvents="none" />)}
            {[...layout.links].sort((a, b) => b.width - a.width).map((link) => <path
              key={`hit:${link.key}`} d={linkPath(link)} fill="none" stroke="transparent"
              strokeWidth={Math.max(link.width, 12)} pointerEvents="stroke"
              aria-label={`${linkLabel(link, labels)}${link.stage === 3 ? "，未追问直接到达" : ""}，${link.count} 个关联对象`}
              onPointerEnter={(event) => showLinkTooltip(event, link)}
              onPointerMove={(event) => showLinkTooltip(event, link)}
              onPointerLeave={() => setHoveredLink(null)} />)}
            {layout.nodes.map((node) => <g key={node.key}>
              <rect x={node.x} y={node.y} width="16" height={node.height} rx="4" fill={COLORS[node.key]} />
              <title>{`${labels[node.key]}：${node.count}`}</title>
              <text className="conversion-node-label" x={node.column === 0 ? node.x - 10 : node.x + 26}
                y={node.y + node.height / 2} dominantBaseline="middle"
                textAnchor={node.column === 0 ? "end" : "start"}>{labels[node.key]} {node.count}</text>
            </g>)}
          </svg></div>
          {hoveredLink && <div className="conversion-link-tooltip" role="tooltip"
            style={{ left: hoveredLink.x, top: hoveredLink.y,
              transform: hoveredLink.y < 100 ? "translate(-50%, 14px)" : "translate(-50%, calc(-100% - 14px))" }}>
            <div className="conversion-link-tooltip-route">{linkLabel(hoveredLink.link, labels)}</div>
            {hoveredLink.link.stage === 3 && <div className="conversion-link-tooltip-kind">未追问，直接到达</div>}
            <div className="conversion-link-tooltip-value"><strong>{hoveredLink.link.count}</strong> 个关联对象</div>
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
            <p>已追问：「仅沟通」会话已追问，且更新时间在所选日期内。</p>
            <p>简历投递成功：「有交换」会话中已核实“对方已同意，您的附件简历已发送给对方”，且入库时间在所选日期内。</p>
            <p>简历投递失败：未核实到附件简历发送的其余关联对象；包含尚未投递或结果未确认，并非已确认发送失败数。</p>
            <p>流线按关联岗位或会话去重；旧会话需重新扫描「有交换」才能核实附件简历发送。</p>
          </> : <p>{data.basis_version === 4
            ? "这张图按旧口径把「有交换」当作投递线索，不代表已确认投递。重新扫描「有交换」后，点击「生成桑基图」使用附件简历发送确认口径。"
            : "这张图按旧统计口径生成。点击「生成桑基图」后，将按当前岗位与会话状态重新统计。"}</p>}
        </div>
      </div></>
        : <p className="conversion-message">{restoring ? "正在恢复上次生成的桑基图…" : "选择日期后，点击“生成桑基图”查看该时间段的转化情况。"}</p>}
    </div>
  </section>;
}
