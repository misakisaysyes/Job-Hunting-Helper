import { useEffect, useRef, useState } from "react";
import { requestJson, jsonOptions } from "./api/client";
import { deleteJob } from "./api/jobs";
import WorkspaceSidebar from "./components/WorkspaceSidebar";
import CollectionPage from "./features/collection/CollectionPage";
import MonitoringPage from "./features/monitoring/MonitoringPage";
import WorkbenchHome from "./features/workbench/WorkbenchHome";
import ConfigurationPage from "./features/config/ConfigurationPage";
import { useAlertSound } from "./hooks/useAlertSound";
import { claimTaskCompletion, createTaskCompletionTracker } from "./lib/taskCompletion";

const PAGE_SIZE = 15;
const DEFAULT_SCORE_THRESHOLD = 71;
export default function App() {
  const { playConfirmationAlert } = useAlertSound();
  const collectionCompletion = useRef(createTaskCompletionTracker());
  const [activeView, setActiveView] = useState("workbench");
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [statusCounts, setStatusCounts] = useState(null);
  const [scoreThreshold, setScoreThreshold] = useState(DEFAULT_SCORE_THRESHOLD);
  const [thresholdWarning, setThresholdWarning] = useState("");
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const requestSequence = useRef(0);
  const [loading, setLoading] = useState(true);
  const loadingRef = useRef(loading);
  const listViewRef = useRef({ page, query, statusFilter });
  loadingRef.current = loading;
  listViewRef.current = { page, query, statusFilter };
  const [deleting, setDeleting] = useState("");
  const [expanded, setExpanded] = useState("");
  const [error, setError] = useState("");
  const [collection, setCollection] = useState({ status: "idle", message: "", counts: {} });
  const [collectionError, setCollectionError] = useState("");
  const [statusReady, setStatusReady] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);

  function updateCollection(state) {
    if (collectionCompletion.current.observe(state)) {
      void claimTaskCompletion(`collection:${state.run_id}`).then((claimed) => {
        if (claimed) playConfirmationAlert();
      });
    }
    setCollection(state);
  }

  async function load(nextPage, nextQuery = query, nextStatus = statusFilter, { silent = false } = {}) {
    const sequence = ++requestSequence.current;
    if (!silent) {
      loadingRef.current = true;
      setLoading(true);
    }
    try {
      const params = new URLSearchParams({ limit: PAGE_SIZE, offset: nextPage * PAGE_SIZE });
      if (nextQuery.trim()) params.set("query", nextQuery.trim());
      if (nextStatus) params.set("status", nextStatus);
      const data = await requestJson(`/api/jobs?${params}`);
      if (sequence !== requestSequence.current) return;
      if (!data.jobs.length && nextPage > 0 && data.total > 0) {
        await load(Math.ceil(data.total / PAGE_SIZE) - 1, nextQuery, nextStatus, { silent });
        return;
      }
      setJobs(data.jobs);
      setTotal(data.total);
      setStatusCounts(data.status_counts || null);
      setError("");
      if (Number.isFinite(data.score_threshold) && data.score_threshold >= 0 && data.score_threshold <= 100) {
        setScoreThreshold(data.score_threshold);
        setThresholdWarning("");
      } else {
        setScoreThreshold(DEFAULT_SCORE_THRESHOLD);
        setThresholdWarning("当前服务未返回评分门槛，暂按默认 71 分标色。重启 ./web/start.sh 后会读取 config.py 中的配置。");
      }
      setPage(nextPage);
    } catch (cause) {
      if (sequence === requestSequence.current) {
        setError(cause.message || "加载失败");
        if (!silent) {
          setJobs([]);
          setTotal(0);
          setStatusCounts(null);
        }
      }
    } finally {
      if (!silent && sequence === requestSequence.current) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    let active = true;
    requestJson("/api/collections/current")
      .then((state) => { if (active) updateCollection(state); })
      .catch((cause) => { if (active) setCollectionError(cause.message || "无法读取采集状态"); })
      .finally(() => { if (active) setStatusReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    requestSequence.current += 1;
    loadingRef.current = true;
    setLoading(true);
    const timer = window.setTimeout(() => load(0, query, statusFilter), query ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [query, statusFilter]);

  useEffect(() => {
    if (activeView !== "collection") return;
    let active = true;
    let timer;
    async function pollJobs() {
      if (!loadingRef.current) {
        await load(page, query, statusFilter, { silent: true });
      }
      if (active) timer = window.setTimeout(pollJobs, 3000);
    }
    timer = window.setTimeout(pollJobs, 3000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [activeView, page, query, statusFilter]);

  useEffect(() => {
    if (activeView !== "collection" && collection.status !== "running") return;
    let active = true;
    let timer;
    async function pollCollection() {
      let keepPolling = activeView === "collection";
      try {
        const state = await requestJson("/api/collections/current");
        if (!active) return;
        updateCollection(state);
        setCollectionError("");
        keepPolling = activeView === "collection" || state.status === "running";
        if (state.status !== "running" && collection.status === "running") {
          const current = listViewRef.current;
          if (!loadingRef.current) void load(current.page, current.query, current.statusFilter, { silent: true });
        }
      } catch (cause) {
        if (active) setCollectionError(cause.message || "无法读取采集状态");
      } finally {
        if (active && keepPolling) timer = window.setTimeout(pollCollection, 3000);
      }
    }
    if (activeView === "collection") void pollCollection();
    else timer = window.setTimeout(pollCollection, 2000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [activeView, collection.status]);

  async function startCollection() {
    setStarting(true);
    setCollectionError("");
    try {
      const state = await requestJson("/api/collections", jsonOptions("POST"));
      updateCollection(state);
    } catch (cause) {
      setCollectionError(cause.message || "启动采集失败");
    } finally {
      setStarting(false);
    }
  }

  async function stopCollection() {
    setStopping(true);
    setCollectionError("");
    try {
      const state = await requestJson("/api/collections/stop", jsonOptions("POST"));
      updateCollection(state);
    } catch (cause) {
      setCollectionError(cause.message || "中断采集失败");
    } finally {
      setStopping(false);
    }
  }

  async function remove(job) {
    const key = `${job.source_platform}:${job.source_job_id}`;
    if (!window.confirm(`确定删除「${job.title || job.source_job_id}」吗？删除后，下次采集可能再次抓到该岗位。`)) return;
    setDeleting(key);
    setError("");
    try {
      await deleteJob(job);
      if (expanded === key) setExpanded("");
      await load(Math.min(page, Math.max(0, Math.ceil((total - 1) / PAGE_SIZE) - 1)));
    } catch (cause) {
      setError(cause.message || "删除失败");
    } finally {
      setDeleting("");
    }
  }

  function updateJob(updated) {
    setJobs((current) => current.map((job) =>
      job.source_platform === updated.source_platform && job.source_job_id === updated.source_job_id
        ? updated : job));
  }

  return (
    <div className="app-shell">
      <WorkspaceSidebar activeView={activeView} onViewChange={setActiveView} />
      <div className="workspace-main">
        {activeView === "workbench" ? (
          <WorkbenchHome
            onOpenCollection={() => setActiveView("collection")}
            onOpenMonitoring={() => setActiveView("monitoring")} />
        ) : activeView === "collection" ? (
          <CollectionPage jobs={jobs} total={total} statusCounts={statusCounts}
            page={page} pageSize={PAGE_SIZE}
            loading={loading} scoreThreshold={scoreThreshold}
            expanded={expanded} onExpanded={setExpanded} deleting={deleting}
            onRemove={remove} onJobChange={updateJob} onPage={load}
            query={query} onQueryChange={setQuery} statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter} collection={collection}
            collectionError={collectionError} error={error} thresholdWarning={thresholdWarning}
            statusReady={statusReady} starting={starting} stopping={stopping}
            onStart={startCollection} onStop={stopCollection} />
        ) : activeView === "monitoring" ? <MonitoringPage /> : <ConfigurationPage />}
      </div>
    </div>
  );
}
