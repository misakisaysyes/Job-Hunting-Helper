import { useRef } from "react";
import { downloadSvg } from "../../lib/downloadSvg";

const phaseColors = {
  data: ["#e6f2ff", "#4183cf"],
  prefilter: ["#fff0d8", "#c38731"],
  score: ["#ecebff", "#6b63bd"],
  greeting: ["#e8f7ee", "#4f9b6a"],
};

const svgStyles = `
  text{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
  .timeline-heading{font-size:26px;font-weight:700;fill:#263a55}
  .timeline-sub{font-size:13px;fill:#647a94}
  .timeline-axis{font-size:14px;fill:#4d6481}
  .timeline-row{font-size:13px;font-weight:700;fill:#3b526e}
  .timeline-phase{font-size:12px;font-weight:700}
  .timeline-manual{font-size:18px;font-weight:700;fill:#725aab}
  .timeline-hint{font-size:12px;fill:#72869c}
`;

function rowsForLimit(limit) {
  if (limit === 1) return [{ label: "岗位 1", center: 205 }];
  if (limit === 2) return [
    { label: "岗位 1", center: 205 },
    { label: "岗位 2", center: 285 },
  ];
  return [
    { label: "岗位 1", center: 205 },
    { label: "岗位 2", center: 285 },
    { label: limit ? `岗位 ${limit}` : "后续岗位", center: 425 },
  ];
}

function TimelineRow({ row, phases }) {
  let current = 180;
  const segments = phases.map((phase) => {
    const x = current;
    current += phase.width;
    const [fill, color] = phase.active ? phaseColors[phase.kind] : ["#f1f4f7", "#9aa9b9"];
    return <g key={phase.kind}>
      <rect x={x} y={row.center - 22} width={phase.width} height="44"
        fill={fill} stroke={color} strokeWidth="1" strokeDasharray={phase.active ? undefined : "5 3"} />
      <text className="timeline-phase" x={x + phase.width / 2} y={row.center + 5}
        textAnchor="middle" fill={color}>{phase.title}</text>
    </g>;
  });
  return <g>
    <text className="timeline-row" x="130" y={row.center + 5}>{row.label}</text>
    {segments}
  </g>;
}

export default function CollectionTimelinePreview({ settings, preview }) {
  const svgRef = useRef(null);
  const collection = settings.collection;
  const ai = settings.ai;
  const limit = preview.collection.target_jobs;
  const scoreOn = Boolean(ai.use_ai_score);
  const greetingOn = scoreOn && Boolean(ai.use_ai_greeting);
  const source = collection.mode === "recommend"
    ? `推荐流 · ${collection.encrypt_expect_id.length} 组期望`
    : `搜索流 · ${collection.keywords.length} 个关键词`;
  const summary = [
    source,
    `每组最多 ${preview.collection.max_pages} 页`,
    `本轮目标采集数：${limit}`,
    `AI 评分：${scoreOn ? `开启（最多 ${preview.ai.ai_api_concurrency} 路并发，门槛 ${preview.ai.score_threshold} 分）` : "关闭"}`,
    `自动生成招呼：${greetingOn ? "开启" : "关闭"}`,
  ].join("  ·  ");
  const phases = [
    { title: "数据抓取", width: 150, kind: "data", active: true },
    { title: "预筛", width: 125, kind: "prefilter", active: true },
    { title: "AI 评分", width: 145, kind: "score", active: scoreOn },
    { title: "生成招呼", width: 155, kind: "greeting", active: greetingOn },
  ];
  const rows = rowsForLimit(limit);

  return <div className="workbench-preview-diagram">
    <div className="workbench-preview-diagram-heading">
      <h3>采集任务</h3>
      <button type="button" onClick={() => downloadSvg(svgRef.current, "collection-workflow.svg")}>下载 SVG</button>
    </div>
    <div className="workbench-batch-preview-scroll">
      <svg ref={svgRef} viewBox="0 0 1500 620" role="img" aria-labelledby="collection-timeline-title collection-timeline-desc">
        <title id="collection-timeline-title">采集任务：批量岗位时间线</title>
        <desc id="collection-timeline-desc">纵轴为批量处理容量，横轴为时间。多条岗位按相同阶段对齐展示；采集结束后由人工审核、修改并发送招呼。</desc>
        <style>{svgStyles}</style>
        <rect x="1" y="1" width="1498" height="618" rx="18" fill="#fff" stroke="#dae5f0" />
        <text className="timeline-heading" x="34" y="47">采集任务 · 批量岗位时间线</text>
        <text className="timeline-sub" x="34" y="76">每一行代表一条岗位；相同阶段对齐，展示一轮采集如何批量处理岗位。</text>
        <rect x="25" y="106" width="1450" height="480" rx="16" fill="#f8fbff" stroke="#e0eaf4" />
        <text className="timeline-sub" x="38" y="139">{summary}</text>
        <line x1="105" y1="530" x2="105" y2="170" stroke="#6b829d" strokeWidth="2" />
        <path d="M 100 178 L 105 166 L 110 178 Z" fill="#6b829d" />
        <line x1="105" y1="530" x2="1435" y2="530" stroke="#6b829d" strokeWidth="2" />
        <path d="M 1424 525 L 1437 530 L 1424 535 Z" fill="#6b829d" />
        <text className="timeline-axis" x="30" y="165">目标采集数</text>
        <text className="timeline-axis" x="76" y="192" textAnchor="end">{limit || "不限"}</text>
        <text className="timeline-axis" x="92" y="535" textAnchor="end">0</text>
        <text className="timeline-axis" x="1448" y="535">时间</text>
        <line x1="820" y1="165" x2="820" y2="530" stroke="#8ca1b8"
          strokeWidth="2" strokeDasharray="7 6" />
        <text className="timeline-axis" x="820" y="158" textAnchor="middle">采集结束</text>
        {rows.length === 3 && (limit === 0 || limit > 3) && <g>
          <text className="timeline-axis" x="470" y="365" textAnchor="middle">···</text>
        </g>}
        {rows.map((row) => <TimelineRow key={row.label} row={row} phases={phases} />)}
        <text className="timeline-manual" x="1120" y="300" textAnchor="middle">人工审核、修改、发送招呼</text>
        <line x1="910" y1="330" x2="1350" y2="330" stroke="#725aab" strokeWidth="3" />
        <path d="M 1338 322 L 1354 330 L 1338 338 Z" fill="#725aab" />
        <text className="timeline-hint" x="190" y="560">
          {scoreOn ? "横条按流程对齐；实际逐条抓取，AI 评分可与后续岗位抓取并发。"
            : "横条按流程对齐；实际逐条抓取并预筛。灰色虚线阶段表示未启用。"}
        </text>
        <text className="timeline-hint" x="34" y="604">
          {scoreOn
            ? "图中只示意进入人工处理的岗位；预筛未通过的岗位退出，低分岗位默认过滤（可手动放行）。灰色虚线阶段表示未启用。"
            : "图中只示意进入人工处理的岗位；预筛未通过的岗位在预筛阶段退出。行数为示意，实际数量由配置决定。"}
        </text>
      </svg>
    </div>
  </div>;
}
