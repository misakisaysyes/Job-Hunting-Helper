import { useEffect, useState } from "react";
import { confirmFilterDelete, deleteConversationRecord, deleteFilterRecord, getCurrentMonitoring, listConversations, listFilterCandidates, openConversation, openFilterCandidate, sendFollowup, startMonitoring, terminateMonitoring } from "../../api/monitoring";
import ConversationList from "./ConversationList";
import ConversationDetails from "./ConversationDetails";
import { savedFollowupText } from "./conversationDisplay";
import FilterCandidateList from "./FilterCandidateList";
import MonitoringStatus from "./MonitoringStatus";
import useMonitoringBatch from "./useMonitoringBatch";

const PAGE_SIZE = 20;

export default function MonitoringPage() {
  const [monitoring, setMonitoring] = useState({ status: "idle", message: "", counts: {} });
  const [statusReady, setStatusReady] = useState(false);
  const [starting, setStarting] = useState(false);
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
  const [openingConversation, setOpeningConversation] = useState("");
  const [openNotice, setOpenNotice] = useState(null);
  const [busyFollowup, setBusyFollowup] = useState("");
  const [followupDrafts, setFollowupDrafts] = useState({});
  const [expandedConversation, setExpandedConversation] = useState("");
  const [busyTerminate, setBusyTerminate] = useState("");
  const [busyConversationDelete, setBusyConversationDelete] = useState("");
  const [filterCandidates, setFilterCandidates] = useState([]);
  const [filterTotal, setFilterTotal] = useState(0);
  const [filterPage, setFilterPage] = useState(0);
  const [filterSearch, setFilterSearch] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterLoading, setFilterLoading] = useState(true);
  const [filterRefreshVersion, setFilterRefreshVersion] = useState(0);
  const [filterError, setFilterError] = useState("");
  const [busyFilter, setBusyFilter] = useState("");
  const [busyFilterRecordDelete, setBusyFilterRecordDelete] = useState("");
  const [filterNotice, setFilterNotice] = useState(null);
  const [openingFilterCandidate, setOpeningFilterCandidate] = useState("");

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
    items: conversations.map((item) => ({ ...item, saved_followup_text: item.followup_text,
      followup_text: followupText(item) })), page,
    filters: `${conversationQuery}\u0000${conversationStatus}\u0000${followupStatus}`,
    loading: listLoading || Boolean(busyFollowup || busyTerminate || busyConversationDelete || openingConversation),
    onReload: () => setListVersion((version) => version + 1),
    actions: {
      followup: {
        label: "追问", preview: true, stopOnFailure: true,
        eligible: (item) => Boolean(item.followup_eligible && item.followup_text.trim()
          && item.followup_text.trim().length <= 300),
        perform: (item) => sendFollowup(item.platform, item.conversation_id,
          { text: item.followup_text, anchor_id: item.followup_anchor_id,
            expected_text: item.saved_followup_text }),
      },
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
    items: filterCandidates, page: filterPage,
    filters: `${filterQuery}\u0000${filterStatus}`,
    loading: filterLoading || Boolean(busyFilter || busyFilterRecordDelete || openingFilterCandidate),
    onReload: () => setListVersion((version) => version + 1),
    actions: {
      filter: {
        label: "删除会话", stopOnFailure: true,
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

  useEffect(() => {
    const timer = window.setTimeout(() => setConversationQuery(conversationSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [conversationSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => setFilterQuery(filterSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [filterSearch]);

  useEffect(() => { setExpandedConversation(""); }, [page, conversationQuery, conversationStatus, followupStatus]);

  useEffect(() => {
    let active = true;
    getCurrentMonitoring()
      .then((state) => { if (active) { setMonitoring(state); setTaskError(""); } })
      .catch((cause) => { if (active) setTaskError(cause.message || "无法读取监测状态"); })
      .finally(() => { if (active) setStatusReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setListLoading(true);
    setListError("");
    listConversations(PAGE_SIZE, page * PAGE_SIZE, {
      query: conversationQuery,
      conversation_status: conversationStatus, followup_status: followupStatus,
    })
      .then((data) => {
        if (!active) return;
        if (!data.conversations.length && page > 0) {
          setPage(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1));
          return;
        }
        setConversations(data.conversations);
        setTotal(data.total);
      })
      .catch((cause) => { if (active) setListError(cause.message || "无法加载会话列表"); })
      .finally(() => { if (active) setListLoading(false); });
    return () => { active = false; };
  }, [page, listVersion, conversationRefreshVersion, conversationQuery, conversationStatus, followupStatus]);

  useEffect(() => {
    let active = true;
    setFilterLoading(true);
    setFilterError("");
    listFilterCandidates(PAGE_SIZE, filterPage * PAGE_SIZE, {
      query: filterQuery, status: filterStatus,
    })
      .then((data) => {
        if (!active) return;
        if (!data.candidates.length && filterPage > 0) {
          setFilterPage(Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1));
          return;
        }
        setFilterCandidates(data.candidates);
        setFilterTotal(data.total);
      })
      .catch((cause) => { if (active) setFilterError(cause.message || "无法加载过滤候选"); })
      .finally(() => { if (active) setFilterLoading(false); });
    return () => { active = false; };
  }, [filterPage, listVersion, filterRefreshVersion, filterQuery, filterStatus]);

  useEffect(() => {
    const deadlines = conversations.map((item) => Date.parse(item.followup_cooldown_until))
      .filter(Number.isFinite);
    if (!deadlines.length) return;
    const delay = Math.min(2_147_483_647, Math.max(0, Math.min(...deadlines) - Date.now()) + 1000);
    const timer = window.setTimeout(() => setConversationRefreshVersion((version) => version + 1), delay);
    return () => window.clearTimeout(timer);
  }, [conversations]);

  useEffect(() => {
    if (monitoring.status !== "running") return;
    let active = true;
    let timer;
    async function poll() {
      try {
        const state = await getCurrentMonitoring();
        if (!active) return;
        setMonitoring(state);
        setTaskError("");
        if (state.status !== "running") {
          setPage(0);
          setFilterPage(0);
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
  }, [monitoring.status]);

  async function start() {
    setStarting(true);
    setTaskError("");
    try {
      const state = await startMonitoring();
      setMonitoring(state);
      if (state.status !== "running") {
        setPage(0);
        setFilterPage(0);
        setListVersion((version) => version + 1);
      }
    } catch (cause) {
      setTaskError(cause.message || "启动监测失败");
    } finally {
      setStarting(false);
    }
  }

  async function openChat(conversation) {
    const key = `${conversation.platform}:${conversation.conversation_id}`;
    setOpeningConversation(key);
    setOpenNotice(null);
    try {
      const result = await openConversation(conversation.platform, conversation.conversation_id);
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
      setOpenNotice({ type: "error", text: cause.message || "追问操作失败，请刷新查看状态" });
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
      setFilterNotice({ type: "error", text: cause.message || "过滤操作失败，请刷新查看状态" });
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
      const result = await openFilterCandidate(candidate.platform, candidate.conversation_id);
      setFilterNotice({ type: "success", text: result.message || "已打开会话" });
    } catch (cause) {
      setFilterNotice({ type: "error", text: cause.message || "打开会话失败" });
    } finally {
      setOpeningFilterCandidate("");
    }
  }

  return (
    <main className="content monitoring-page">
      <div className="heading">
        <div>
          <p className="eyebrow">WORKBENCH</p>
          <h1>监测任务</h1>
          <p className="subtitle">监测「仅沟通」中未读、已读未回的会话，并从「新招呼」筛出命中公司排除词的会话。</p>
        </div>
        <button className="start-button" type="button" onClick={start}
          disabled={!statusReady || starting || monitoring.status === "running"}>
          {!statusReady ? "准备中…" : starting ? "启动中…" : monitoring.status === "running" ? "正在监测…" : "开始监测"}
        </button>
      </div>

      <MonitoringStatus monitoring={monitoring} />
      {taskError && <div className="error" role="alert">{taskError}</div>}

      <div className="section-heading monitoring-section-heading">
        <div><h2>会话进展</h2>
          <p>查看「仅沟通」中未读或已读未回会话的追问进展、入库和更新时间。</p></div>
      </div>
      <div className="monitoring-filters monitoring-conversation-filters" aria-label="会话进展筛选">
        <input type="search" aria-label="搜索会话" placeholder="搜索岗位、公司、HR 或会话 ID"
          disabled={Boolean(conversationBatch.busy)}
          value={conversationSearch} onChange={(event) => { setPage(0); setConversationSearch(event.target.value); }} />
        <select aria-label="筛选HR检阅状态" value={conversationStatus} disabled={Boolean(conversationBatch.busy)}
          onChange={(event) => { setPage(0); setConversationStatus(event.target.value); }}>
          <option value="">全部HR检阅状态</option>
          <option value="unread">未读</option>
          <option value="read_no_reply">已读未回</option>
        </select>
        <select aria-label="筛选追问状态" value={followupStatus} disabled={Boolean(conversationBatch.busy)}
          onChange={(event) => { setPage(0); setFollowupStatus(event.target.value); }}>
          <option value="">全部追问状态</option>
          <option value="available">可追问</option>
          <option value="cooling">冷冻中</option>
          <option value="pending_review">追问语待发送</option>
          <option value="followed_up">已追问</option>
          <option value="failed">发送失败</option>
          <option value="unknown">结果不明</option>
        </select>
      </div>
      {listError && <div className="error" role="alert">{listError}</div>}
      {openNotice && <div className={openNotice.type === "error" ? "error" : "notice"}
        role={openNotice.type === "error" ? "alert" : "status"}>{openNotice.text}</div>}
      <ConversationList conversations={conversations} total={total} page={page} pageSize={PAGE_SIZE}
        loading={listLoading} onPage={setPage}
        onRefresh={() => setConversationRefreshVersion((version) => version + 1)}
        expanded={expandedConversation} onExpanded={setExpandedConversation}
        renderDetails={(conversation) => <ConversationDetails conversation={conversation}
          draft={followupText(conversation)} onDraftChange={(text) => editFollowup(conversation, text)}
          onChange={updateFollowup} onSend={handleFollowup} onOpen={openChat}
          openingConversation={openingConversation}
          onTerminate={handleTerminate} terminatingConversation={busyTerminate}
          disabled={Boolean(busyFollowup || busyTerminate || busyConversationDelete || conversationBatch.busy || filterBatch.busy)}
          onBusyChange={(action) => setBusyFollowup(action
            ? `${conversation.platform}:${conversation.conversation_id}` : "")} />}
        onDelete={handleConversationDelete} busyAction={busyFollowup || busyTerminate || busyConversationDelete || filterBatch.busy}
        busyDelete={busyConversationDelete}
        batch={conversationBatch} onBatchModeChange={(enabled) => {
          if (filterBatch.busy) return;
          if (enabled) { filterBatch.close(); conversationBatch.setBatchMode(true); }
          else conversationBatch.close();
        }}
        filtered={Boolean(conversationQuery || conversationStatus || followupStatus)} />
      {conversationBatch.preview && <div className="followup-modal-backdrop"
        onMouseDown={() => !conversationBatch.busy && conversationBatch.setPreview(null)}>
        <section className="followup-modal monitoring-batch-preview" role="dialog" aria-modal="true"
          aria-labelledby="batch-followup-title" onMouseDown={(event) => event.stopPropagation()}>
          <div className="followup-modal-heading"><div><h3 id="batch-followup-title">批量追问语预览</h3>
            <p>将发送 {conversationBatch.preview.eligible.length} 条；跳过 {conversationBatch.preview.skipped} 条。</p></div>
            <button type="button" aria-label="关闭批量追问语预览" onClick={() => conversationBatch.setPreview(null)}>×</button>
          </div>
          <div className="monitoring-batch-preview-list">{conversationBatch.preview.eligible.map((item) =>
            <div key={`${item.platform}:${item.conversation_id}`}>
              <strong>{item.company || "未知公司"} · {item.recruiter || "未知 HR"}</strong>
              <p>{item.followup_text}</p>
            </div>)}</div>
          <div className="followup-modal-actions">
            <button type="button" onClick={() => conversationBatch.setPreview(null)}>取消</button>
            <button className="start-button" type="button" onClick={() => conversationBatch.execute(
              conversationBatch.preview.actionName, conversationBatch.preview.eligible, conversationBatch.preview.skipped
            )}>发送追问</button>
          </div>
        </section>
      </div>}
      <div className="section-heading monitoring-section-heading">
        <div><h2>新招呼过滤</h2>
          <p>扫描时只读取「新招呼」列表；确认删除后会先打开目标会话，再从菜单删除。打开会话可能清除未读标记。</p></div>
      </div>
      <div className="monitoring-filters monitoring-filter-candidates" aria-label="新招呼过滤筛选">
        <input type="search" aria-label="搜索过滤候选" placeholder="搜索公司、HR、岗位、排除词或会话 ID"
          disabled={Boolean(filterBatch.busy)}
          value={filterSearch} onChange={(event) => { setFilterPage(0); setFilterSearch(event.target.value); }} />
        <select aria-label="筛选过滤状态" value={filterStatus} disabled={Boolean(filterBatch.busy)}
          onChange={(event) => { setFilterPage(0); setFilterStatus(event.target.value); }}>
          <option value="">全部处理状态</option>
          <option value="pending_review">待审核删除</option>
          <option value="deleting">删除中</option>
          <option value="deleted">已删除</option>
          <option value="failed">删除失败</option>
          <option value="unknown">结果不明</option>
          <option value="stale">已失效</option>
        </select>
      </div>
      {filterError && <div className="error" role="alert">{filterError}</div>}
      {filterNotice && <div className={filterNotice.type === "error" ? "error" : "notice"}
        role={filterNotice.type === "error" ? "alert" : "status"}>{filterNotice.text}</div>}
      <FilterCandidateList candidates={filterCandidates} total={filterTotal} page={filterPage}
        pageSize={PAGE_SIZE} loading={filterLoading} onPage={setFilterPage}
        onRefresh={() => setFilterRefreshVersion((version) => version + 1)}
        onAction={handleFilter} onOpen={openFilteredChat} onDelete={handleFilterRecordDelete}
        openingCandidate={openingFilterCandidate} busy={busyFilter || busyFilterRecordDelete || conversationBatch.busy}
        busyDelete={busyFilterRecordDelete}
        batch={filterBatch} onBatchModeChange={(enabled) => {
          if (conversationBatch.busy) return;
          if (enabled) { conversationBatch.close(); filterBatch.setBatchMode(true); }
          else filterBatch.close();
        }}
        filtered={Boolean(filterQuery || filterStatus)} />
    </main>
  );
}
