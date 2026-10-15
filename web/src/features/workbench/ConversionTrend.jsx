import { useState } from "react";

const SERIES = [
  { key: "greeted", label: "主动投递 · 池内", color: "#1978e6", soft: "#edf5ff" },
  { key: "hr_initiated", label: "HR 联系 · 池外", color: "#447eae", soft: "#edf5fa" },
  { key: "followups", label: "已追问", color: "#7557b8", soft: "#f4f0fb" },
  { key: "deliveries", label: "简历投递成功", color: "#168457", soft: "#eaf7ef" },
];
const WIDTH = 1080;
const HEIGHT = 390;
const LEFT = 60;
const RIGHT = 28;
const TOP = 42;
const BOTTOM = 58;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;

function formatDay(value) {
  const [, month, day] = value.split("-");
  return `${Number(month)}月${Number(day)}日`;
}

function isWeekend(value) {
  const day = new Date(`${value}T00:00:00Z`).getUTCDay();
  return day === 0 || day === 6;
}

function niceStep(max) {
  const rough = Math.max(max, 1) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const scaled = rough / magnitude;
  const rounded = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return Math.max(1, rounded * magnitude);
}

function xAt(index, count) {
  return LEFT + (count === 1 ? PLOT_WIDTH / 2 : index / (count - 1) * PLOT_WIDTH);
}

function yAt(value, ceiling) {
  return TOP + PLOT_HEIGHT * (1 - value / ceiling);
}

export default function ConversionTrend({ dailyMetrics }) {
  const [activeKeys, setActiveKeys] = useState(["greeted"]);
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const activeSeries = SERIES.filter((series) => activeKeys.includes(series.key));
  const totals = Object.fromEntries(SERIES.map((series) => [series.key,
    dailyMetrics.reduce((sum, day) => sum + day[series.key], 0)]));
  const max = dailyMetrics.reduce((peak, day) => Math.max(peak,
    ...activeSeries.map((series) => day[series.key])), 0);
  const step = niceStep(max);
  const ceiling = Math.max(step, Math.ceil(max / step) * step);
  const yTicks = Array.from({ length: Math.round(ceiling / step) + 1 }, (_, index) => index * step);
  const labelCount = dailyMetrics.length <= 1 ? 1 : dailyMetrics.length <= 35 ? 3
    : dailyMetrics.length <= 90 ? 5 : 7;
  const labelIndexes = [...new Set(Array.from({ length: labelCount }, (_, index) =>
    labelCount === 1 ? 0 : Math.round(index * (dailyMetrics.length - 1) / (labelCount - 1))))];
  const markIndexes = dailyMetrics.length <= 45
    ? dailyMetrics.map((_, index) => index) : labelIndexes;
  const showDots = dailyMetrics.length <= 45;
  const hoveredDay = dailyMetrics[hoveredIndex] || null;
  const peakIndexes = [];
  if (activeSeries.length === 1 && showDots && max > 0) {
    const key = activeSeries[0].key;
    const ranked = dailyMetrics.map((day, index) => ({ index, value: day[key] }))
      .filter(({ value }) => value > 0)
      .sort((a, b) => b.value - a.value || a.index - b.index);
    for (const candidate of ranked) {
      if (peakIndexes.every((index) => Math.abs(index - candidate.index) >= 3)) {
        peakIndexes.push(candidate.index);
        if (peakIndexes.length === 2) break;
      }
    }
  }

  function toggleSeries(key) {
    setHoveredIndex(null);
    setActiveKeys((current) => current.includes(key)
      ? current.length > 1 ? current.filter((item) => item !== key) : current
      : [...current, key]);
  }

  function selectDay(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    const index = Math.round(ratio * (dailyMetrics.length - 1));
    setHoveredIndex((current) => current === index ? current : index);
  }

  return <div className="conversion-trend">
    <div className="conversion-trend-themes" role="group" aria-label="选择折线主题，可多选">
      {SERIES.map((series) => <button key={series.key} type="button"
        className={`conversion-trend-theme${activeKeys.includes(series.key) ? " active" : ""}`}
        style={{ "--theme-color": series.color, "--theme-soft": series.soft }}
        aria-pressed={activeKeys.includes(series.key)} onClick={() => toggleSeries(series.key)}>
        <span className="conversion-trend-theme-dot" aria-hidden="true" />
        <span>{series.label}</span><strong>{totals[series.key]}</strong>
      </button>)}
    </div>
    <div className="conversion-trend-panel">
      <div className="conversion-trend-heading">
        <strong>每日沟通记录</strong>
        <span>{hoveredDay ? formatDay(hoveredDay.date) : "点击主题可多选 · 移入折线查看每日数值"}</span>
      </div>
      <div className="conversion-trend-readout">
        {hoveredDay ? activeSeries.map((series) => <span key={series.key}
          style={{ "--theme-color": series.color }}>
          <i aria-hidden="true" />{series.label} <strong>{hoveredDay[series.key]}</strong>
        </span>) : <span>一日一点；空心点表示周末</span>}
      </div>
      <div className="conversion-trend-scroll"><svg className="conversion-trend-svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
        aria-label={`${dailyMetrics[0].date} 至 ${dailyMetrics.at(-1).date} 每日沟通记录折线图；${activeSeries.map((series) => `${series.label}合计 ${totals[series.key]} 条`).join("，")}`}>
        {yTicks.map((value) => <g key={`y:${value}`}>
          <line className="conversion-trend-grid" x1={LEFT} x2={WIDTH - RIGHT}
            y1={yAt(value, ceiling)} y2={yAt(value, ceiling)} />
          <text className="conversion-trend-axis-label" x={LEFT - 16}
            y={yAt(value, ceiling) + 4} textAnchor="end">{value}</text>
        </g>)}
        {markIndexes.map((index) => <line key={`mark:${index}`} className="conversion-trend-mark"
          x1={xAt(index, dailyMetrics.length)} x2={xAt(index, dailyMetrics.length)}
          y1={HEIGHT - BOTTOM + 2} y2={HEIGHT - BOTTOM + 13} />)}
        {labelIndexes.map((index) => <text key={`date:${index}`} className="conversion-trend-axis-label"
          x={xAt(index, dailyMetrics.length)} y={HEIGHT - 20}
          textAnchor={index === 0 && dailyMetrics.length > 1 ? "start"
            : index === dailyMetrics.length - 1 && dailyMetrics.length > 1 ? "end" : "middle"}>
          {formatDay(dailyMetrics[index].date)}
        </text>)}
        {activeSeries.map((series) => <g key={series.key}>
          <path className="conversion-trend-line"
            d={dailyMetrics.map((day, index) => `${index ? "L" : "M"} ${xAt(index, dailyMetrics.length).toFixed(2)} ${yAt(day[series.key], ceiling).toFixed(2)}`).join(" ")}
            stroke={series.color} />
          {showDots && dailyMetrics.map((day, index) => <circle key={day.date}
            className="conversion-trend-dot" cx={xAt(index, dailyMetrics.length)}
            cy={yAt(day[series.key], ceiling)} r={peakIndexes.includes(index) && activeSeries.length === 1 ? 5.5 : 3.2}
            fill={isWeekend(day.date) ? "#fbfdff" : series.color} stroke={series.color} />)}
        </g>)}
        {activeSeries.length === 1 && peakIndexes.map((index) => <text key={`peak:${index}`}
          className="conversion-trend-peak" x={xAt(index, dailyMetrics.length)}
          y={Math.max(22, yAt(dailyMetrics[index][activeSeries[0].key], ceiling) - 15)} textAnchor="middle">
          {dailyMetrics[index][activeSeries[0].key]}
        </text>)}
        {hoveredDay && <g>
          <line className="conversion-trend-hover-line" x1={xAt(hoveredIndex, dailyMetrics.length)}
            x2={xAt(hoveredIndex, dailyMetrics.length)} y1={TOP} y2={HEIGHT - BOTTOM} />
          {activeSeries.map((series) => <circle key={series.key} className="conversion-trend-hover-dot"
            cx={xAt(hoveredIndex, dailyMetrics.length)} cy={yAt(hoveredDay[series.key], ceiling)}
            r="6" fill={series.color} />)}
        </g>}
        <rect x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT}
          fill="transparent" onPointerMove={selectDay} onPointerDown={selectDay}
          onPointerLeave={() => setHoveredIndex(null)} />
      </svg></div>
      <p className="conversion-mobile-hint">左右滑动查看完整日期范围</p>
      <p className="conversion-trend-note">每天按记录的更新时间或首次入库时间归日，未必是实际招呼、追问或发送日；不同主题可能包含同一岗位或会话。</p>
    </div>
  </div>;
}
