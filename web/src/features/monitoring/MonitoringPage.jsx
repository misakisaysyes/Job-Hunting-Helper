import { useEffect, useRef, useState } from "react";
import { confirmFilterDelete, deleteConversationRecord, deleteExchangedConversation, deleteFilterRecord, generateFollowup, getCurrentMonitoring, listConversations, listExchangedConversations, listFilterCandidates, openConversation, openExchangedConversation, openFilterCandidate, sendFollowup, startMonitoring, terminateMonitoring } from "../../api/monitoring";
import { useAlertSound } from "../../hooks/useAlertSound";
import { claimTaskCompletion, createTaskCompletionTracker } from "../../lib/taskCompletion";
import ConversationList from "./ConversationList";
import ConversationDetails from "./ConversationDetails";
import { savedFollowupText } from "./conversationDisplay";
import FilterCandidateList from "./FilterCandidateList";
import ExchangeConversationList from "./ExchangeConversationList";
import MonitoringStatus from "./MonitoringStatus";
import useMonitoringBatch from "./useMonitoringBatch";
import { createFollowupGenerationAction } from "./followupGeneration";
import { createFollowupSendingAction } from "./followupSending";
import { PAGE_SIZE } from "../../lib/pagination";

const MONITORING_TABS = [
  ["communicated", "仅沟通"],
  ["exchanged", "有交换"],
  ["new_greetings", "新会话"],
];

function hasRunningMonitoring(state) {
  return state.status === "running" || Object.values(state.batches || {}).some((batch) => batch?.status === "running");
}

function selectedCountLabel(label, value, selected, count) {
  return value === selected && typeof count === "number" ? `${label}（${count}）` : label;
}

export default function MonitoringPage() {
  const { playConfirmationAlert } = useAlertSound();
  const monitoringCompletion = useRef(createTaskCompletionTracker());
  const [monitoring, setMonitoring] = useState({ status: "idle", message: "", counts: {} });
  const [activePhase, setActivePhase] = useState("communicated");
  const [statusReady, setStatusReady] = useState(false);
  const [starting, setStarting] = useState("");
  const [taskError, setTaskError] = useState("");
  const [conversations, setConversations] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [conversationSearch, setConversationSearch] = useState("");
  const [conversationQuery, setConversationQuery] = useState("");
  const [conversationStatus, setConversationStatus] = useState("");
  const [followupStatus, setFollowupStatus] = useState("");
  const [listVersion, setListVersion] = useState(0);
  const [conversationRefreshVersion, setConversationRefreshVersion] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [conversationResultKey, setConversationResultKey] = useState("");
  const [openingConversation, setOpeningConversation] = useState("");
  const [openNotice, setOpenNotice] = useState(null);
  const [busyFollowup, setBusyFollowup] = useState("");
  const [followupDrafts, setFollowupDrafts] = useState({});
  const [expandedConversation, setExpandedConversation] = useState("");
  const [busyTerminate, setBusyTerminate] = useState("");
  const [busyConversationDelete, setBusyConversationDelete] = useState("");
  const [exchanges, setExchanges] = useState([]);
  const [exchangeTotal, setExchangeTotal] = useState(0);
  const [exchangePage, setExchangePage] = useState(0);
  const [exchangeSearch, setExchangeSearch] = useState("");
  const [exchangeQuery, setExchangeQuery] = useState("");
  const [exchangePoolStatus, setExchangePoolStatus] = useState("");
  const [exchangeLoading, setExchangeLoading] = useState(true);
  const [exchangeError, setExchangeError] = useState("");
  const [exchangeResultKey, setExchangeResultKey] = useState("");
  const [exchangeNotice, setExchangeNotice] = useState(null);
  const [openingExchange, setOpeningExchange] = useState("");
  const [deletingExchange, setDeletingExchange] = useState("");
  const [filterCandidates, setFilterCandidates] = useState([]);
  const [filterTotal, setFilterTotal] = useState(0);
  const [filterPage, setFilterPage] = useState(0);
  const [filterSearch, setFilterSearch] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterLoading, setFilterLoading] = useState(true);
  const [filterError, setFilterError] = useState("");
  const [filterResultKey, setFilterResultKey] = useState("");
  const [busyFilter, setBusyFilter] = useState("");
  const [busyFilterRecordDelete, setBusyFilterRecordDelete] = useState("");
  const [filterNotice, setFilterNotice] = useState(null);
  const [openingFilterCandidate, setOpeningFilterCandidate] = useState("");
  const monitoringBusy = hasRunningMonitoring(monitoring);
  const conversationKey = JSON.stringify([page, conversationQuery, conversationStatus, followupStatus]);
  const exchangeKey = JSON.stringify([exchangePage, exchangeQuery, exchangePoolStatus]);
  const filterKey = JSON.stringify([filterPage, filterQuery, filterStatus]);
  const visibleConversations = conversationResultKey === conversationKey ? conversations : [];
  const visibleExchanges = exchangeResultKey === exchangeKey ? exchanges : [];
  const visibleFilterCandidates = filterResultKey === filterKey ? filterCandidates : [];
  const conversationCount = listError ? "—" : conversationResultKey === conversationKey ? total : "…";
  const exchangeCount = exchangeError ? "—" : exchangeResultKey === exchangeKey ? exchangeTotal : "…";
  const filterCount = filterError ? "—" : filterResultKey === filterKey ? filterTotal : "…";

  function updateMonitoring(state, merge = false) {
    if (monitoringCompletion.current.observe(state)) {
      void claimTaskCompletion(`monitoring:${state.run_id}`).then((claimed) => {
        if (claimed) playConfirmationAlert();
      });
    }
    setMonitoring((previous) => merge ? { ...previous, ...state } : state);
  }

  function updateBatchTask(action, state) {
    setMonitoring((previous) => ({ ...previous, batches: { ...previous.batches, [action]: state } }));
  }

  function followupText(conversation) {
    const draft = followupDrafts[`${conversation.platform}:${conversation.conversation_id}`];
    if (draft && draft.anchor === conversation.followup_anchor_id
        && draft.savedText === conversation.followup_text
        && conversation.followup_status === "pending_review") return draft.text;
    return savedFollowupText(conversation);
  }

  function editFollowup(conversation, text) {
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setFollowupDrafts((previous) => ({ ...previous, [key]: {
      anchor: conversation.followup_anchor_id, savedText: conversation.followup_text, text,
    } }));
  }

  const conversationBatch = useMonitoringBatch({
    items: visibleConversations.map((item) => ({ ...item, saved_followup_text: item.followup_text,
      followup_text: followupText(item) })), page,
    filters: `${conversationQuery}\u0000${conversationStatus}\u0000${followupStatus}`,
    persistSelectionAcrossPages: true,
    loading: listLoading || monitoringBusy || Boolean(busyFollowup || busyTerminate || busyConversationDelete || openingConversation),
    onTaskChange: updateBatchTask,
    onReload: () => setListVersion((version) => version + 1),
    actions: {
      generate: createFollowupGenerationAction(generateFollowup, updateFollowup),
      followup: createFollowupSendingAction(sendFollowup),
      terminate: {
        label: "终止会话", eligible: (item) => item.followup_status !== "sending",
        confirm: (count, skipped) => `确定终止所选 ${count} 条会话吗？后续扫描会跳过这些会话。${skipped ? `另有 ${skipped} 条将跳过。` : ""}`,
        perform: (item) => terminateMonitoring(item.platform, item.conversation_id),
      },
      delete: {
        label: "删除", eligible: (item) => item.followup_status !== "sending",
        confirm: (count, skipped) => `确定从本地数据库删除所选 ${count} 条会话及消息吗？这不会删除 BOSS 会话；后续扫描可能再次入库。${skipped ? `另有 ${skipped} 条将跳过。` : ""}`,
        perform: (item) => deleteConversationRecord(item.platform, item.conversation_id),
      },
    },
  });
  const filterBatch = useMonitoringBatch({
    items: visibleFilterCandidates, page: filterPage,
    filters: `${filterQuery}\u0000${filterStatus}`,
    persistSelectionAcrossPages: true,
    loading: filterLoading || monitoringBusy || Boolean(busyFilter || busyFilterRecordDelete || openingFilterCandidate),
    onTaskChange: updateBatchTask,
    onReload: () => setListVersion((version) => version + 1),
    actions: {
      filter: {
        label: "删除会话", stopOnFailure: true,
        batchAction: "delete",
        toBatchItem: (item) => ({ platform: item.platform, conversation_id: item.conversation_id,
          confirm_still_present: item.status === "unknown" }),
        eligible: (item) => ["pending_review", "failed", "unknown"].includes(item.status),
        confirm: (count, skipped) => `确定从 BOSS「新招呼」删除所选 ${count} 条会话吗？系统会重新核对目标；删除结果不明的记录将再次尝试。${skipped ? `另有 ${skipped} 条不可删除的候选将跳过。` : ""}`,
        perform: (item) => confirmFilterDelete(item.platform, item.conversation_id,
          item.status === "unknown"),
      },
      delete: {
        label: "删除记录", eligible: (item) => item.status !== "deleting",
        confirm: (count, skipped) => `确定从本地数据库删除所选 ${count} 条过滤记录吗？这不会删除 BOSS 会话；后续扫描可能再次入库。${skipped ? `另有 ${skipped} 条将跳过。` : ""}`,
        perform: (item) => deleteFilterRecord(item.platform, item.conversation_id),
      },
    },
  });
  const exchangeBatch = useMonitoringBatch({
    items: visibleExchanges, page: exchangePage,
    filters: `${exchangeQuery}\u0000${exchangePoolStatus}`,
    persistSelectionAcrossPages: true,
    loading: exchangeLoading || monitoringBusy || Boolean(openingExchange || deletingExchange),
    onReload: () => setListVersion((version) => version + 1),
    actions: {
      delete: {
        label: "删除会话", stopOnFailure: true, eligible: () => true,
        confirm: (count) => `确定从 BOSS「有交换」删除所选 ${count} 条会话吗？BOSS 会同时删除这些聊天记录。若某条删除结果不明，批量操作会停止。`,
        perform: (item) => deleteExchangedConversation(item.platform, item.conversation_id),
      },
    },
  });

  useEffect(() => {
    const timer = window.setTimeout(() => setConversationQuery(conversationSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [conversationSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => setFilterQuery(filterSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [filterSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => setExchangeQuery(exchangeSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [exchangeSearch]);

  useEffect(() => { setExpandedConversation(""); }, [page, conversationQuery, conversationStatus, followupStatus]);

  useEffect(() => {
    let active = true;
    getCurrentMonitoring()
      .then((state) => { if (active) {
        updateMonitoring(state);
        setTaskError("");
        if (state.status === "running" && MONITORING_TABS.some(([key]) => key === state.phase)) {
          setActivePhase(state.phase);
        }
      } })
      .catch((cause) => { if (active) setTaskError(cause.message || "无法读取监测状态"); })
      .finally(() => { if (active) setStatusReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    setListLoading(true);
    setListError("");
    async function refresh() {
      try {
        const data = await listConversations(PAGE_SIZE, page * PAGE_SIZE, {
          query: conversationQuery,
          conversation_status: conversationStatus, followup_status: followupStatus,
        });
        if (!active) return;
        if (!data.conversations.length && page > 0) {
          setPage(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1));
          return;
        }
        setConversations(data.conversations);
        setTotal(data.total);
        setConversationResultKey(conversationKey);
        setListError("");
      } catch (cause) {
        if (active) {
          setConversations([]);
          setTotal(0);
          setConversationResultKey(conversationKey);
          setListError(cause.message || "无法加载会话列表");
        }
      } finally {
        if (active) {
          setListLoading(false);
          timer = window.setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [page, listVersion, conversationRefreshVersion, conversationQuery, conversationStatus, followupStatus]);

  useEffect(() => {
    let active = true;
    let timer;
    setFilterLoading(true);
    setFilterError("");
    async function refresh() {
      try {
        const data = await listFilterCandidates(PAGE_SIZE, filterPage * PAGE_SIZE, {
          query: filterQuery, status: filterStatus,
        });
        if (!active) return;
        if (!data.candidates.length && filterPage > 0) {
          setFilterPage(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1));
          return;
        }
        setFilterCandidates(data.candidates);
        setFilterTotal(data.total);
        setFilterResultKey(filterKey);
        setFilterError("");
      } catch (cause) {
        if (active) {
          setFilterCandidates([]);
          setFilterTotal(0);
          setFilterResultKey(filterKey);
          setFilterError(cause.message || "无法加载过滤候选");
        }
      } finally {
        if (active) {
          setFilterLoading(false);
          timer = window.setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [filterPage, listVersion, filterQuery, filterStatus]);

  useEffect(() => {
    let active = true;
    let timer;
    setExchangeLoading(true);
    setExchangeError("");
    async function refresh() {
      try {
        const data = await listExchangedConversations(
          PAGE_SIZE, exchangePage * PAGE_SIZE, exchangeQuery, exchangePoolStatus);
        if (!active) return;
        if (!data.conversations.length && exchangePage > 0) {
          setExchangePage(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1));
          return;
        }
        setExchanges(data.conversations);
        setExchangeTotal(data.total);
        setExchangeResultKey(exchangeKey);
        setExchangeError("");
      } catch (cause) {
        if (active) {
          setExchanges([]);
          setExchangeTotal(0);
          setExchangeResultKey(exchangeKey);
          setExchangeError(cause.message || "无法加载有交换会话");
        }
      } finally {
        if (active) {
          setExchangeLoading(false);
          timer = window.setTimeout(refresh, 3000);
        }
      }
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [exchangePage, exchangeQuery, exchangePoolStatus, listVersion]);

  useEffect(() => {
    const deadlines = conversations.map((item) => Date.parse(item.followup_cooldown_until))
      .filter(Number.isFinite);
    if (!deadlines.length) return;
    const delay = Math.min(2_147_483_647, Math.max(0, Math.min(...deadlines) - Date.now()) + 1000);
    const timer = window.setTimeout(() => setConversationRefreshVersion((version) => version + 1), delay);
    return () => window.clearTimeout(timer);
  }, [conversations]);

  useEffect(() => {
    if (!monitoringBusy) return;
    let active = true;
    let timer;
    async function poll() {
      try {
        const state = await getCurrentMonitoring();
        if (!active) return;
        updateMonitoring(state);
        setTaskError("");
        if (!hasRunningMonitoring(state)) {
          if (["all", "communicated"].includes(state.phase)) setPage(0);
          if (["all", "new_greetings"].includes(state.phase)) setFilterPage(0);
          if (state.phase === "exchanged") setExchangePage(0);
          setListVersion((version) => version + 1);
          return;
        }
      } catch (cause) {
        if (active) setTaskError(cause.message || "无法读取监测状态");
      }
      if (active) timer = window.setTimeout(poll, 2000);
    }
    timer = window.setTimeout(poll, 2000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [monitoringBusy]);

  async function start(phase) {
    setActivePhase(phase);
    setStarting(phase);
    setTaskError("");
    try {
      const state = await startMonitoring(phase);
      updateMonitoring(state, true);
      if (state.status !== "running") {
        if (phase === "communicated") setPage(0);
        if (phase === "new_greetings") setFilterPage(0);
        if (phase === "exchanged") setExchangePage(0);
        setListVersion((version) => version + 1);
      }
    } catch (cause) {
      setTaskError(cause.message || "启动监测失败");
    } finally {
      setStarting("");
    }
  }

  async function openChat(conversation) {
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setOpeningConversation(key);
    setOpenNotice(null);
    try {
      const result = await openConversation(conversation.platform, conversation.conversation_id,
        conversation.source_job_id);
      setOpenNotice({ type: "success", text: result.message || "已在 Chrome 打开对应的 BOSS 会话" });
    } catch (cause) {
      setOpenNotice({ type: "error", text: cause.message || "打开会话失败" });
    } finally {
      setOpeningConversation("");
    }
  }

  function updateFollowup(conversation, { preserveDraft = false } = {}) {
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    if (!preserveDraft) {
      setFollowupDrafts((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
    }
    setConversations((previous) => previous.map((item) =>
      item.platform === conversation.platform && item.conversation_id === conversation.conversation_id
        ? { ...item, ...conversation } : item));
  }

  async function handleFollowup(conversation, text) {
    setOpenNotice(null);
    try {
      const result = await sendFollowup(conversation.platform, conversation.conversation_id,
        { text, anchor_id: conversation.followup_anchor_id, expected_text: conversation.followup_text });
      setOpenNotice({ type: "success", text: result.message || "追问已发送" });
      setListVersion((version) => version + 1);
      return result;
    } catch (cause) {
      setOpenNotice({ type: "error", text: cause.message || "追问操作失败，列表会自动更新状态" });
      setListVersion((version) => version + 1);
      throw cause;
    }
  }

  async function handleTerminate(conversation) {
    if (!window.confirm(`终止会话「${conversation.recruiter || conversation.company || conversation.conversation_id}」？后续监测将跳过这条会话。`)) return;
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setBusyTerminate(key);
    setOpenNotice(null);
    try {
      await terminateMonitoring(conversation.platform, conversation.conversation_id);
      setOpenNotice({ type: "success", text: "已终止会话，后续扫描将跳过该会话" });
      setExpandedConversation("");
      setListVersion((version) => version + 1);
    } catch (cause) {
      setOpenNotice({ type: "error", text: cause.message || "终止会话失败" });
    } finally {
      setBusyTerminate("");
    }
  }

  async function handleConversationDelete(conversation) {
    if (!window.confirm(`删除「${conversation.recruiter || conversation.company || conversation.conversation_id}」的本地会话记录？\n这不会删除 BOSS 会话。后续监测如果仍扫描到该会话，可能再次入库。`)) return;
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setBusyConversationDelete(key);
    setOpenNotice(null);
    try {
      const result = await deleteConversationRecord(conversation.platform, conversation.conversation_id);
      setOpenNotice({ type: "success", text: result.message || "本地会话记录已删除" });
      setExpandedConversation("");
    } catch (cause) {
      setOpenNotice({ type: "error", text: cause.message || "删除本地会话记录失败" });
    } finally {
      setBusyConversationDelete("");
      setListVersion((version) => version + 1);
    }
  }

  async function handleFilter(candidate) {
    if (!window.confirm(
      `确认从 BOSS「新招呼」删除「${candidate.company} · ${candidate.recruiter}」的会话吗？\n命中公司排除词：${candidate.matched_term}${candidate.status === "unknown" ? "\n上次删除结果不明，系统会重新核对目标并再次尝试。" : ""}`)) return;
    const key = `${candidate.platform}:${candidate.conversation_id}`;
    setBusyFilter(key);
    setFilterNotice(null);
    try {
      const result = await confirmFilterDelete(candidate.platform, candidate.conversation_id,
        candidate.status === "unknown");
      setFilterNotice({ type: "success", text: result.message || "会话已删除" });
    } catch (cause) {
      setFilterNotice({ type: "error", text: cause.message || "过滤操作失败，列表会自动更新状态" });
    } finally {
      setBusyFilter("");
      setListVersion((version) => version + 1);
    }
  }

  async function handleFilterRecordDelete(candidate) {
    if (!window.confirm(`删除「${candidate.company} · ${candidate.recruiter || "未知 HR"}」的本地过滤记录？\n这不会删除 BOSS 会话。后续扫描若仍命中排除词，可能再次入库。`)) return;
    const key = `${candidate.platform}:${candidate.conversation_id}`;
    setBusyFilterRecordDelete(key);
    setFilterNotice(null);
    try {
      const result = await deleteFilterRecord(candidate.platform, candidate.conversation_id);
      setFilterNotice({ type: "success", text: result.message || "本地过滤记录已删除" });
    } catch (cause) {
      setFilterNotice({ type: "error", text: cause.message || "删除本地过滤记录失败" });
    } finally {
      setBusyFilterRecordDelete("");
      setListVersion((version) => version + 1);
    }
  }

  async function openFilteredChat(candidate) {
    const key = `${candidate.platform}:${candidate.conversation_id}`;
    setOpeningFilterCandidate(key);
    setFilterNotice(null);
    try {
      const result = await openFilterCandidate(candidate.platform, candidate.conversation_id,
        candidate.source_job_id);
      setFilterNotice({ type: "success", text: result.message || "已在 BOSS「新招呼」列表中定位会话" });
    } catch (cause) {
      setFilterNotice({ type: "error", text: cause.message || "打开会话失败" });
    } finally {
      setOpeningFilterCandidate("");
    }
  }

  async function openExchangeChat(conversation) {
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setOpeningExchange(key);
    setExchangeNotice(null);
    try {
      const result = await openExchangedConversation(conversation.platform, conversation.conversation_id,
        conversation.source_job_id);
      setExchangeNotice({ type: "success", text: result.message || "已在 Chrome 打开「有交换」中的目标会话" });
    } catch (cause) {
      setExchangeNotice({ type: "error", text: cause.message || "定位有交换会话失败" });
    } finally {
      setOpeningExchange("");
    }
  }

  async function handleExchangeDelete(conversation) {
    if (!window.confirm(`确定从 BOSS「有交换」删除「${conversation.company || conversation.recruiter || conversation.conversation_id}」的会话吗？\nBOSS 会同时删除这段聊天记录。`)) return;
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setDeletingExchange(key);
    setExchangeNotice(null);
    try {
      const result = await deleteExchangedConversation(conversation.platform, conversation.conversation_id);
      setExchanges((previous) => previous.filter((item) =>
        `${item.platform}:${item.conversation_id}` !== key));
      setExchangeTotal((previous) => Math.max(0, previous - 1));
      setExchangeNotice({ type: "success", text: result.message || "已从 BOSS「有交换」删除会话" });
      setListVersion((version) => version + 1);
    } catch (cause) {
      setExchangeNotice({ type: "error", text: cause.message || "删除有交换会话失败" });
    } finally {
      setDeletingExchange("");
    }
  }

  return (
    <main className="content monitoring-page">
      <div className="heading">
        <div>
          <h1>监测任务</h1>
          <p className="subtitle">选择左侧任务，启动扫描并查看对应会话和过滤结果。</p>
        </div>
      </div>

      <div className="monitoring-workspace">
        <nav className="monitoring-workspace-nav" aria-label="选择监测任务">
          {MONITORING_TABS.map(([key, label]) => <button key={key} type="button"
            className={activePhase === key ? "active" : ""}
            aria-pressed={activePhase === key} onClick={() => {
              if (key !== "communicated" && conversationBatch.preview) conversationBatch.setPreview(null);
              setActivePhase(key);
            }}>{label}</button>)}
        </nav>
        <div className="monitoring-workspace-content">
          <MonitoringStatus monitoring={monitoring} activePhase={activePhase}
            onStart={start} starting={starting} statusReady={statusReady} monitoringBusy={monitoringBusy} />
          {taskError && <div className="error" role="alert">{taskError}</div>}
        </div>
      </div>

      <section className="monitoring-list-panel" aria-label="监测会话列表">
          <div className="monitoring-workspace-pane" hidden={activePhase !== "communicated"}>
      <div className="section-heading monitoring-section-heading">
        <div><h2>「仅沟通」会话列表</h2>
          <p>查看「仅沟通」会话的追问进展；本轮未扫描到的历史会话仍会保留，并显示上次扫描状态。</p></div>
      </div>
      {listError && <div className="error" role="alert">{listError}</div>}
      {openNotice && <div className={openNotice.type === "error" ? "error" : "notice"}
        role={openNotice.type === "error" ? "alert" : "status"}>{openNotice.text}</div>}
      <ConversationList toolbar={
          <div className="monitoring-filters monitoring-conversation-filters" aria-label="「仅沟通」会话列表筛选">
          <input type="search" aria-label="搜索会话" placeholder="搜索岗位、公司、HR 或会话 ID"
            disabled={Boolean(conversationBatch.busy)}
            value={conversationSearch} onChange={(event) => { setPage(0); setConversationSearch(event.target.value); }} />
          <div className="monitoring-select">
            <select aria-label="筛选HR检阅状态"
              value={conversationStatus} disabled={Boolean(conversationBatch.busy)}
              onChange={(event) => { setPage(0); setConversationStatus(event.target.value); }}>
              <option value="">{selectedCountLabel("全部HR检阅状态", "", conversationStatus, conversationCount)}</option>
              <option value="unread">{selectedCountLabel("未读", "unread", conversationStatus, conversationCount)}</option>
              <option value="read_no_reply">{selectedCountLabel("已读未回", "read_no_reply", conversationStatus, conversationCount)}</option>
            </select>
          </div>
          <div className="monitoring-select">
            <select aria-label="筛选追问状态"
              value={followupStatus} disabled={Boolean(conversationBatch.busy)}
              onChange={(event) => { setPage(0); setFollowupStatus(event.target.value); }}>
              <option value="">{selectedCountLabel("全部追问状态", "", followupStatus, conversationCount)}</option>
              <option value="available">{selectedCountLabel("可追问", "available", followupStatus, conversationCount)}</option>
              <option value="unavailable">{selectedCountLabel("不可追问", "unavailable", followupStatus, conversationCount)}</option>
              <option value="sending">{selectedCountLabel("发送中", "sending", followupStatus, conversationCount)}</option>
              <option value="incomplete">{selectedCountLabel("追问未完成", "incomplete", followupStatus, conversationCount)}</option>
            </select>
          </div>
        </div>
      } conversations={visibleConversations}
        total={conversationResultKey === conversationKey ? total : 0} page={page} pageSize={PAGE_SIZE}
        loading={listLoading || conversationResultKey !== conversationKey} onPage={setPage}
        expanded={expandedConversation} onExpanded={setExpandedConversation}
        renderDetails={(conversation) => <ConversationDetails conversation={conversation}
          draft={followupText(conversation)} onDraftChange={(text) => editFollowup(conversation, text)}
          onChange={updateFollowup} onSend={handleFollowup} onOpen={openChat}
          openingConversation={openingConversation}
          onTerminate={handleTerminate} terminatingConversation={busyTerminate}
          disabled={monitoringBusy || Boolean(busyFollowup || busyTerminate || busyConversationDelete || conversationBatch.busy || filterBatch.busy || exchangeBatch.busy)}
          onBusyChange={(action) => setBusyFollowup(action
            ? `${conversation.platform}:${conversation.conversation_id}` : "")} />}
        onDelete={handleConversationDelete} busyAction={monitoringBusy || busyFollowup || busyTerminate || busyConversationDelete || filterBatch.busy || exchangeBatch.busy}
        busyDelete={busyConversationDelete}
        batch={conversationBatch} onBatchModeChange={(enabled) => {
          if (filterBatch.busy || exchangeBatch.busy) return;
          if (enabled) { filterBatch.close(); exchangeBatch.close(); conversationBatch.setBatchMode(true); }
          else conversationBatch.close();
        }}
        filtered={Boolean(conversationQuery || conversationStatus || followupStatus)} />
      {conversationBatch.preview && <div className="followup-modal-backdrop"
        onMouseDown={() => !conversationBatch.busy && conversationBatch.setPreview(null)}>
        <section className="followup-modal monitoring-batch-preview" role="dialog" aria-modal="true"
          aria-labelledby="batch-followup-title" onMouseDown={(event) => event.stopPropagation()}>
          <div className="followup-modal-heading"><div><h3 id="batch-followup-title">批量追问语预览</h3>
            <p>将处理 {conversationBatch.preview.eligible.length} 条，跳过 {conversationBatch.preview.skipped} 条未准备好追问草稿或不符合条件的会话。追问语为空的会记为失败并继续下一条。</p></div>
            <button type="button" aria-label="关闭批量追问语预览" onClick={() => conversationBatch.setPreview(null)}>×</button>
          </div>
          <div className="monitoring-batch-preview-list">{conversationBatch.preview.eligible.map((item) =>
            <div key={`${item.platform}:${item.conversation_id}`}>
              <strong>{item.company || "未知公司"} · {item.recruiter || "未知 HR"}</strong>
              <p>{item.followup_text.trim() || "追问语为空：本条将记为失败，不会发送"}</p>
            </div>)}</div>
          <div className="followup-modal-actions">
            <button type="button" onClick={() => conversationBatch.setPreview(null)}>取消</button>
            <button className="start-button" type="button" onClick={() => conversationBatch.execute(
              conversationBatch.preview.actionName, conversationBatch.preview.eligible, conversationBatch.preview.skipped
            )}>发送追问</button>
          </div>
        </section>
      </div>}
          </div>
          <div className="monitoring-workspace-pane" hidden={activePhase !== "exchanged"}>
      <div className="section-heading monitoring-section-heading">
        <div><h2>「有交换」会话列表</h2>
          <p>从 BOSS「更多 → 有交换」读取会话；池外岗位保持池外，不会自动入池。</p></div>
      </div>
      {exchangeError && <div className="error" role="alert">{exchangeError}</div>}
      {exchangeNotice && <div className={exchangeNotice.type === "error" ? "error" : "notice"}
        role={exchangeNotice.type === "error" ? "alert" : "status"}>{exchangeNotice.text}</div>}
      <ExchangeConversationList toolbar={
          <div className="monitoring-filters" aria-label="「有交换」会话列表筛选">
          <input type="search" aria-label="搜索有交换会话" placeholder="搜索岗位、公司、HR 或会话 ID"
            disabled={Boolean(exchangeBatch.busy)}
            value={exchangeSearch} onChange={(event) => { setExchangePage(0); setExchangeSearch(event.target.value); }} />
          <div className="monitoring-select">
            <select aria-label="筛选有交换岗位池状态"
              value={exchangePoolStatus} disabled={Boolean(exchangeBatch.busy)}
              onChange={(event) => { setExchangePage(0); setExchangePoolStatus(event.target.value); }}>
              <option value="">{selectedCountLabel("全部岗位", "", exchangePoolStatus, exchangeCount)}</option>
              <option value="inside">{selectedCountLabel("池内", "inside", exchangePoolStatus, exchangeCount)}</option>
              <option value="outside">{selectedCountLabel("池外", "outside", exchangePoolStatus, exchangeCount)}</option>
            </select>
          </div>
        </div>
      } conversations={visibleExchanges}
        total={exchangeResultKey === exchangeKey ? exchangeTotal : 0} page={exchangePage}
        pageSize={PAGE_SIZE} loading={exchangeLoading || exchangeResultKey !== exchangeKey} onPage={setExchangePage}
        filtered={Boolean(exchangeQuery || exchangePoolStatus)}
        onOpen={openExchangeChat} onDelete={handleExchangeDelete}
        opening={openingExchange} deleting={deletingExchange}
        busy={monitoringBusy || conversationBatch.busy || filterBatch.busy}
        batch={exchangeBatch} onBatchModeChange={(enabled) => {
          if (conversationBatch.busy || filterBatch.busy) return;
          if (enabled) { conversationBatch.close(); filterBatch.close(); exchangeBatch.setBatchMode(true); }
          else exchangeBatch.close();
        }} />
          </div>
          <div className="monitoring-workspace-pane" hidden={activePhase !== "new_greetings"}>
      <div className="section-heading monitoring-section-heading">
        <div><h2>「新招呼」会话列表</h2>
          <p>仅展示「新招呼」中命中公司排除词的会话；确认删除后会先打开目标会话，再从菜单删除。打开会话可能清除未读标记。</p></div>
      </div>
      {filterError && <div className="error" role="alert">{filterError}</div>}
      {filterNotice && <div className={filterNotice.type === "error" ? "error" : "notice"}
        role={filterNotice.type === "error" ? "alert" : "status"}>{filterNotice.text}</div>}
      <FilterCandidateList toolbar={
          <div className="monitoring-filters monitoring-filter-candidates" aria-label="「新招呼」会话列表筛选">
          <input type="search" aria-label="搜索过滤候选" placeholder="搜索公司、HR、岗位、排除词或会话 ID"
            disabled={Boolean(filterBatch.busy)}
            value={filterSearch} onChange={(event) => { setFilterPage(0); setFilterSearch(event.target.value); }} />
          <div className="monitoring-select">
            <select aria-label="筛选过滤状态"
              value={filterStatus} disabled={Boolean(filterBatch.busy)}
              onChange={(event) => { setFilterPage(0); setFilterStatus(event.target.value); }}>
              <option value="">{selectedCountLabel("全部处理状态", "", filterStatus, filterCount)}</option>
              <option value="pending_review">{selectedCountLabel("待审核删除", "pending_review", filterStatus, filterCount)}</option>
              <option value="deleting">{selectedCountLabel("删除中", "deleting", filterStatus, filterCount)}</option>
              <option value="deleted">{selectedCountLabel("已删除", "deleted", filterStatus, filterCount)}</option>
              <option value="failed">{selectedCountLabel("删除失败", "failed", filterStatus, filterCount)}</option>
              <option value="unknown">{selectedCountLabel("结果不明", "unknown", filterStatus, filterCount)}</option>
              <option value="stale">{selectedCountLabel("已失效", "stale", filterStatus, filterCount)}</option>
            </select>
          </div>
        </div>
      } candidates={visibleFilterCandidates}
        total={filterResultKey === filterKey ? filterTotal : 0} page={filterPage}
        pageSize={PAGE_SIZE} loading={filterLoading || filterResultKey !== filterKey} onPage={setFilterPage}
        onAction={handleFilter} onOpen={openFilteredChat} onDelete={handleFilterRecordDelete}
        openingCandidate={openingFilterCandidate} busy={monitoringBusy || busyFilter || busyFilterRecordDelete || conversationBatch.busy || exchangeBatch.busy}
        busyDelete={busyFilterRecordDelete}
        batch={filterBatch} onBatchModeChange={(enabled) => {
          if (conversationBatch.busy || exchangeBatch.busy) return;
          if (enabled) { conversationBatch.close(); exchangeBatch.close(); filterBatch.setBatchMode(true); }
          else filterBatch.close();
        }}
        filtered={Boolean(filterQuery || filterStatus)} />
          </div>
      </section>
    </main>
  );
}
