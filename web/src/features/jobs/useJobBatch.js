import { useEffect, useState } from "react";
import { deleteJob, fetchJob, forceEnd, generateGreeting, scoreJob, sendGreeting, startGreeting } from "../../api/jobs";
import { useAlertSound } from "../../hooks/useAlertSound";
import { canForceEnd, canGenerateGreeting, canOverrideGreetingThreshold, canStartGreeting, matchesListStatus } from "../../lib/jobStatus";
import { jobKey, selectPageJobs, toggleSelectedJob } from "./jobSelection";
import { prepareGreetingJob } from "./prepareGreetingBatch";

export { jobKey } from "./jobSelection";

export default function useJobBatch({ jobs, total, page, pageSize, query, statusFilter,
  onReload, onJobChange, loading, scoreThreshold }) {
  const [batchMode, setBatchMode] = useState(false);
  const [selectedByKey, setSelectedByKey] = useState(() => new Map());
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [notice, setNotice] = useState("");
  const { playConfirmationAlert } = useAlertSound();
  const selectedKeys = new Set(selectedByKey.keys());
  const selectedJobs = [...selectedByKey.values()];
  const allSelected = jobs.length > 0 && jobs.every((job) => selectedByKey.has(jobKey(job)));

  useEffect(() => { setSelectedByKey(new Map()); setNotice(""); }, [query, statusFilter]);

  function toggle(job) {
    setSelectedByKey((previous) => toggleSelectedJob(previous, job));
  }

  function selectAll(checked) {
    setSelectedByKey((previous) => selectPageJobs(previous, jobs, checked));
  }

  function syncSelection(job) {
    setSelectedByKey((previous) => {
      const key = jobKey(job);
      if (!previous.has(key)) return previous;
      const next = new Map(previous);
      if (matchesListStatus(job, statusFilter)) next.set(key, job);
      else next.delete(key);
      return next;
    });
  }

  function close() {
    setBatchMode(false);
    setSelectedByKey(new Map());
    setNotice("");
    setProgress("");
  }

  async function run(action) {
    if (!selectedJobs.length || busy || loading) return;
    let eligible = selectedJobs;
    const belowThreshold = action === "prepare"
      ? selectedJobs.filter((job) => canOverrideGreetingThreshold(job, scoreThreshold)) : [];
    if (action === "score") {
      eligible = selectedJobs.filter((job) => ["scored", "greeting_ready"].includes(job.job_status)
        && job.greeting_send_state === "idle");
    } else if (action === "end") {
      eligible = selectedJobs.filter(canForceEnd);
    } else if (action === "prepare") {
      eligible = selectedJobs.filter((job) => canStartGreeting(job, scoreThreshold)
        || canOverrideGreetingThreshold(job, scoreThreshold) || canGenerateGreeting(job));
    } else if (action === "send") {
      eligible = selectedJobs.filter((job) => job.job_status === "greeting_ready"
        && job.greeting_send_state === "idle" && job.greeting?.trim());
    }
    const skipped = selectedJobs.length - eligible.length;
    const labels = { delete: "删除", end: "结束", score: "评分", prepare: "准备招呼语", send: "发送招呼语" };
    if (!eligible.length) {
      setNotice(`所选岗位中没有可${labels[action]}的岗位。`);
      return;
    }
    if (action === "delete" && !window.confirm(`确定删除所选 ${selectedJobs.length} 个岗位吗？删除后可能再次采集入库。`)) return;
    if (action === "end" && !window.confirm(`确定将 ${eligible.length} 个岗位标记为“已结束（强制中断）”吗？已发送的招呼语不会撤回。${skipped ? `另有 ${skipped} 个岗位将跳过。` : ""}`)) return;
    if (action === "score" && eligible.length && !window.confirm(`确定重新评分 ${eligible.length} 个岗位吗？岗位状态会根据新评分重置，未发送的招呼语草稿会清空。${skipped ? `另有 ${skipped} 个岗位将跳过。` : ""}`)) return;
    if (action === "prepare" && eligible.length) {
      const toTransition = eligible.filter((job) => job.job_status === "scored").length;
      const existingDrafts = eligible.filter((job) => job.greeting?.trim()).length;
      if (!window.confirm(`确定为 ${eligible.length} 个岗位准备招呼语吗？${toTransition
        ? `其中 ${toTransition} 个将先流转到打招呼状态。` : ""}${belowThreshold.length
        ? `其中 ${belowThreshold.length} 个低于当前 ${scoreThreshold} 分门槛，确认后仍会流转。` : ""}${existingDrafts
        ? `${existingDrafts} 个已有草稿会被覆盖。` : ""}${skipped
        ? `另有 ${skipped} 个岗位将跳过。` : ""}只生成并保存草稿，不会发送。`)) return;
    }
    if (action === "send" && eligible.length && !window.confirm(`确定向 ${eligible.length} 个 BOSS 招聘者实际发送已保存的招呼语吗？${skipped ? `另有 ${skipped} 个岗位将跳过。` : ""}`)) return;

    setBusy(action);
    setNotice("");
    setProgress(`正在${labels[action]}：0/${eligible.length}`);
    let success = 0;
    let transitionedWithoutGreeting = 0;
    let presetSent = 0;
    const failures = [];
    const warnings = [];
    let processed = 0;
    const perform = async (job) => {
      try {
        if (action === "delete") await deleteJob(job);
        else if (action === "prepare") {
          const result = await prepareGreetingJob(job, {
            allowBelowThreshold: canOverrideGreetingThreshold(job, scoreThreshold),
            startGreeting, generateGreeting,
            onJobChange: (updated) => { syncSelection(updated); onJobChange(updated); },
          });
          if (!result.generated) {
            if (result.transitioned) transitionedWithoutGreeting += 1;
            throw result.error || new Error("招呼语未生成");
          }
        } else {
          const result = action === "score" ? await scoreJob(job)
            : action === "end" ? await forceEnd(job)
              : await sendGreeting(job, job.greeting);
          if (result.job) {
            syncSelection(result.job);
            onJobChange(result.job);
          }
          if (result.error) throw new Error(result.error);
        }
        success += 1;
      } catch (error) {
        let refreshed = null;
        if (action === "send") {
          try {
            refreshed = await fetchJob(job);
            syncSelection(refreshed);
            onJobChange(refreshed);
          } catch { /* The reported error remains. */ }
        }
        const detail = `${job.title || job.source_job_id}：${error.message || "操作失败"}`;
        if (action === "send" && refreshed?.greeting_send_state === "sent"
          && refreshed.greeting_send_note?.includes("预设招呼语")) {
          presetSent += 1;
          warnings.push(detail);
        } else {
          failures.push(detail);
        }
      } finally {
        processed += 1;
        setProgress(`正在${labels[action]}：${processed}/${eligible.length}`);
      }
    };

    try {
      if (action === "score" || action === "prepare") {
        await Promise.all(eligible.map(perform));
      } else {
        for (const job of eligible) {
          await perform(job);
          // A BOSS preset greeting may affect later jobs, so pause only for that case.
          if (action === "send" && presetSent) break;
        }
      }
      const remaining = eligible.length - processed;
      const skippedTotal = skipped + remaining;
      const summary = action === "prepare"
        ? `准备招呼语完成：生成成功 ${success}，失败 ${failures.length}${transitionedWithoutGreeting
          ? `（其中已流转但未生成 ${transitionedWithoutGreeting}）` : ""}，跳过 ${skippedTotal}。`
        : action === "send" && presetSent
        ? `发送招呼语完成：自定义成功 ${success}，BOSS 预设已发送 ${presetSent}，跳过 ${skippedTotal}，失败 ${failures.length}。`
        : `${labels[action]}完成：成功 ${success}，跳过 ${skippedTotal}，失败 ${failures.length}。`;
      setNotice(summary + [...warnings, ...failures].slice(0, 2).join("；"));
      playConfirmationAlert();
      setSelectedByKey(new Map());
      const nextPage = action === "delete"
        ? Math.min(page, Math.max(0, Math.ceil((total - success) / pageSize) - 1)) : page;
      await onReload(nextPage);
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  return { batchMode, setBatchMode, selectedKeys, selectedJobs, allSelected,
    busy, progress, notice, toggle, selectAll, syncSelection, close, run };
}
