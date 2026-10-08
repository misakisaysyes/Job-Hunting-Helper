import { useEffect, useState } from "react";
import { getTodayTasks } from "../../api/tasks";
import TaskRunOverview from "./TaskRunOverview";
import "./workbench.css";

export default function WorkbenchHome({ onOpenCollection, onOpenMonitoring }) {
  const [today, setToday] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer;
    async function refresh() {
      try {
        const result = await getTodayTasks();
        if (active) {
          setToday(result);
          setError("");
        }
      } catch (cause) {
        if (active) setError(cause.message || "无法读取今日任务");
      } finally {
        if (active) timer = window.setTimeout(refresh, 3000);
      }
    }
    refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, []);

  return <main className="content workbench-home">
    <header className="workbench-intro">
      <p className="eyebrow">WORKBENCH</p>
      <h1>工作台</h1>
      <p>查看今天执行过的采集与监测任务，以及当前进度。</p>
    </header>
    <TaskRunOverview data={today} error={error}
      onOpenCollection={onOpenCollection} onOpenMonitoring={onOpenMonitoring} />
  </main>;
}
