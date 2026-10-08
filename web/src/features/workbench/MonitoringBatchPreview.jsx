import { useRef } from "react";
import { downloadSvg } from "../../lib/downloadSvg";

const positions = [42, 325, 608, 891, 1174];
const colors = {
  normal: ["#fff", "#c8dcf2"],
  decision: ["#fff9f0", "#e8cda8"],
  manual: ["#f5f1ff", "#d7c9f2"],
  result: ["#f0faf5", "#b6ddc7"],
};

function Step({ item, x }) {
  const [fill, stroke] = colors[item.kind || "normal"];
  return <g>
    <rect x={x} y="112" width="224" height="100" rx="13" fill={fill}
      stroke={stroke} strokeWidth="1.5" strokeDasharray={item.planned ? "6 5" : undefined} />
    {item.planned && <text className="monitor-planned" x={x + 211} y="131" textAnchor="end">规划</text>}
    <text className="monitor-card-title" x={x + 16} y="155">{item.title}</text>
    <text className="monitor-card-detail" x={x + 16} y="183">{item.detail}</text>
  </g>;
}

export default function MonitoringBatchPreview({ settings }) {
  const svgRef = useRef(null);
  const items = [
    { title: "扫描两类会话", detail: "仅沟通 / 新招呼" },
    { title: "分别判断", detail: "追问条件 / 公司排除词", kind: "decision" },
    { title: "生成待办", detail: "追问 / 删除候选", kind: "manual" },
    { title: "审核或自动执行", detail: "原文重发 / 删除列表项", kind: "manual" },
    { title: "回写进度", detail: "会话与过滤结果", kind: "result" },
  ];
  return <div className="workbench-preview-diagram">
    <div className="workbench-preview-diagram-heading">
      <h3>监测任务</h3>
      <button type="button" onClick={() => downloadSvg(svgRef.current, "monitoring-workflow.svg")}>下载 SVG</button>
    </div>
    <div className="workbench-batch-preview-scroll">
      <svg ref={svgRef} viewBox="0 0 1500 280" role="img" aria-labelledby="monitor-batch-title monitor-batch-desc">
        <title id="monitor-batch-title">监测任务：批量会话工作流</title>
        <desc id="monitor-batch-desc">扫描仅沟通标签，按回执、冷却时间和追问次数筛选，按配置审核或自动原文重发。</desc>
        <style>{`text{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.monitor-heading{font-size:22px;font-weight:700;fill:#263a55}.monitor-sub{font-size:13px;fill:#647a94}.monitor-card-title{font-size:16px;font-weight:700;fill:#334a65}.monitor-card-detail{font-size:12px;fill:#6e839a}.monitor-planned{font-size:11px;fill:#9076c4}`}</style>
        <rect x="1" y="1" width="1498" height="278" rx="18" fill="#fff" stroke="#dae5f0" />
        <text className="monitor-heading" x="34" y="42">监测任务 · 批量会话</text>
        <text className="monitor-sub" x="34" y="68">独立启动一轮扫描，逐条处理直到扫描范围结束。</text>
        {items.map((item, index) => <g key={item.title}>
          {index > 0 && <g>
            <line x1={positions[index - 1] + 224} y1="162" x2={positions[index] - 7} y2="162"
              stroke="#8daacb" strokeWidth="2" />
            <path d={`M ${positions[index] - 9} 157 L ${positions[index]} 162 L ${positions[index] - 9} 167 Z`} fill="#8daacb" />
          </g>}
          <Step item={item} x={positions[index]} />
        </g>)}
        <text className="monitor-sub" x="34" y="247">同一会话再次扫描时更新回执；只有确认发送成功才增加追问次数。</text>
      </svg>
    </div>
  </div>;
}
