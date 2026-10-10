import { useEffect, useState } from "react";
import { getTaskHistory } from "../../api/tasks";
import { TaskRunCards } from "./TaskRunOverview";

function shiftDate(value, days) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export default function TaskRunHistory() {
  const [selectedDate, setSelectedDate] = useState("");
  const [data, setData] = useState(null);
  const [latestDate, setLatestDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    let timer;
    setLoading(true);
    setData(null);
    setError("");
    async function refresh() {
      try {
        const result = await getTaskHistory(selectedDate);
        if (!active) return;
        setData(result);
        setLatestDate(result.latest_date);
        setError("");
        if (["collection", "monitoring", "monitoring_followup", "monitoring_delete"].some((type) =>
          result[type]?.history.some((run) => run.status === "running"))) {
          timer = window.setTimeout(refresh, 3000);
        }
      } catch (cause) {
        if (active) setError(cause.message || "无法读取历史任务记录");
      } finally {
        if (active) setLoading(false);
      }
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [selectedDate, version]);

  const shownDate = selectedDate || data?.date || "";
  return <section className="workbench-section workbench-history" aria-labelledby="task-history-title">
    <div className="workbench-history-heading">
      <div className="workbench-section-heading">
        <h2 id="task-history-title">历史任务记录</h2>
        <p>{shownDate || "按日期查看"} · 任务按开始时间计入当天，已招呼按确认发送时间统计。</p>
      </div>
      <div className="workbench-history-controls">
        <button type="button" className="toolbar-button" disabled={loading || !shownDate}
          onClick={() => setSelectedDate(shiftDate(shownDate, -1))}>前一天</button>
        <label>日期<input type="date" aria-label="历史任务日期" value={shownDate}
          max={latestDate || undefined} disabled={!latestDate}
          onChange={(event) => { if (event.target.value) setSelectedDate(event.target.value); }} /></label>
        <button type="button" className="toolbar-button"
          disabled={loading || !shownDate || shownDate >= latestDate}
          onClick={() => setSelectedDate(shiftDate(shownDate, 1))}>后一天</button>
        <button type="button" className="toolbar-button" disabled={loading}
          onClick={() => setVersion((value) => value + 1)}>{loading ? "加载中…" : "刷新记录"}</button>
      </div>
    </div>
    {error && <p className="workbench-settings-notice error" role="alert">{error}</p>}
    {loading && <p className="workbench-run-empty" role="status">正在读取历史任务记录…</p>}
    {data && <TaskRunCards data={data} historical />}
  </section>;
}
