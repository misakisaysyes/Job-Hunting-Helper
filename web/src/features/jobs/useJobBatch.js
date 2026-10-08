import { useEffect, useState } from "react";
import { deleteJob, fetchJob, generateGreeting, scoreJob, sendGreeting } from "../../api/jobs";

export const jobKey = (job) => `${job.source_platform}:${job.source_job_id}`;

export default function useJobBatch({ jobs, total, page, pageSize, query, statusFilter,
  onReload, onJobChange, loading }) {
  const [batchMode, setBatchMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState(new Set());
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [notice, setNotice] = useState("");
  const selectedJobs = jobs.filter((job) => selectedKeys.has(jobKey(job)));
  const allSelected = jobs.length > 0 && selectedJobs.length === jobs.length;

  useEffect(() => { setSelectedKeys(new Set()); setNotice(""); }, [page, query, statusFilter]);

  function toggle(job) {
    setSelectedKeys((previous) => {
      const next = new Set(previous);
      const key = jobKey(job);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectAll(checked) {
    setSelectedKeys(checked ? new Set(jobs.map(jobKey)) : new Set());
  }

  function close() {
    setBatchMode(false);
    setSelectedKeys(new Set());
    setNotice("");
    setProgress("");
  }

  async function run(action) {
    if (!selectedJobs.length || busy || loading) return;
    let eligible = selectedJobs;
    if (action === "score") {
      eligible = selectedJobs.filter((job) => ["scored", "greeting_ready"].includes(job.job_status)
        && job.greeting_send_state === "idle");
    } else if (action === "generate") {
      eligible = selectedJobs.filter((job) => job.job_status === "greeting_ready"
        && job.ai_score_status !== "not_scored");
    } else if (action === "send") {
      eligible = selectedJobs.filter((job) => job.job_status === "greeting_ready"
        && job.greeting_send_state === "idle" && job.greeting?.trim());
    }
    const skipped = selectedJobs.length - eligible.length;
    if (action === "delete" && !window.confirm(`确定删除所选 ${selectedJobs.length} 个岗位吗？删除后可能再次采集入库。`)) return;
    if (action === "score" && eligible.length && !window.confirm(`确定重新评分 ${eligible.length} 个岗位吗？岗位状态会根据新评分重置，未发送的招呼语草稿会清空。${skipped ? `另有 ${skipped} 个岗位将跳过。` : ""}`)) return;
    if (action === "generate" && eligible.length && !window.confirm(`确定为 ${eligible.length} 个岗位生成招呼语吗？已有草稿会被覆盖。`)) return;
    if (action === "send" && eligible.length && !window.confirm(`确定向 ${eligible.length} 个 BOSS 招聘者实际发送已保存的招呼语吗？${skipped ? `另有 ${skipped} 个岗位将跳过。` : ""}`)) return;

    const labels = { delete: "删除", score: "评分", generate: "生成招呼语", send: "发送招呼语" };
    setBusy(action);
    setNotice("");
    setProgress(`正在${labels[action]}：0/${eligible.length}`);
    let success = 0;
    const failures = [];
    let processed = 0;
    const perform = async (job) => {
      try {
        if (action === "delete") await deleteJob(job);
        else {
          const result = action === "score" ? await scoreJob(job)
            : action === "generate" ? await generateGreeting(job)
              : await sendGreeting(job, job.greeting);
          if (result.job) onJobChange(result.job);
          if (result.error) throw new Error(result.error);
        }
        success += 1;
      } catch (error) {
        failures.push(`${job.title || job.source_job_id}：${error.message || "操作失败"}`);
        if (action === "send") {
          try { onJobChange(await fetchJob(job)); } catch { /* The reported error remains. */ }
        }
      } finally {
        processed += 1;
        setProgress(`正在${labels[action]}：${processed}/${eligible.length}`);
      }
    };

    try {
      if (action === "score" || action === "generate") {
        await Promise.all(eligible.map(perform));
      } else {
        for (const job of eligible) {
          await perform(job);
          if (action === "send" && failures.length) break;
        }
      }
      const remaining = eligible.length - processed;
      const skippedTotal = skipped + remaining;
      setNotice(`${labels[action]}完成：成功 ${success}，跳过 ${skippedTotal}，失败 ${failures.length}。${failures.slice(0, 2).join("；")}`);
      setSelectedKeys(new Set());
      const nextPage = action === "delete"
        ? Math.min(page, Math.max(0, Math.ceil((total - success) / pageSize) - 1)) : page;
      await onReload(nextPage);
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  return { batchMode, setBatchMode, selectedKeys, selectedJobs, allSelected,
    busy, progress, notice, toggle, selectAll, close, run };
}
