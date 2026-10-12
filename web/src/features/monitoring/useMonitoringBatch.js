import { useEffect, useState } from "react";
import { useAlertSound } from "../../hooks/useAlertSound";
import { getMonitoringBatch, startMonitoringBatch } from "../../api/monitoring";
import { executeMonitoringBatch, executeRecordedMonitoringBatch } from "./monitoringBatch";
import { monitoringKey, selectPageConversations, toggleSelectedConversation } from "./monitoringSelection";

export { monitoringKey } from "./monitoringSelection";

export default function useMonitoringBatch({ items, page, filters, actions, onReload, loading, onTaskChange,
  persistSelectionAcrossPages = false }) {
  const [batchMode, setBatchMode] = useState(false);
  const [selected, setSelected] = useState(new Map());
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState(null);
  const { playConfirmationAlert } = useAlertSound();
  const selectedKeys = new Set(selected.keys());
  const currentItems = new Map(items.map((item) => [monitoringKey(item), item]));
  const selectedItems = persistSelectionAcrossPages
    ? [...selected].map(([key, item]) => currentItems.get(key) || item)
    : items.filter((item) => selectedKeys.has(monitoringKey(item)));
  const allSelected = items.length > 0 && items.every((item) => selectedKeys.has(monitoringKey(item)));

  useEffect(() => { setSelected(new Map()); setNotice(""); }, [filters]);
  useEffect(() => {
    if (!persistSelectionAcrossPages) { setSelected(new Map()); setNotice(""); }
  }, [page, persistSelectionAcrossPages]);

  function toggle(item) {
    setSelected((previous) => toggleSelectedConversation(previous, item));
  }

  function selectAll(checked) {
    setSelected((previous) => selectPageConversations(
      persistSelectionAcrossPages ? previous : new Map(), items, checked));
  }

  function close() {
    if (busy) return;
    setBatchMode(false);
    setSelected(new Map());
    setNotice("");
    setProgress("");
    setPreview(null);
  }

  function prepare(actionName) {
    if (!selectedItems.length || busy || loading) return;
    const action = actions[actionName];
    if (!action) return;
    const eligible = selectedItems.filter(action.eligible);
    const skipped = selectedItems.length - eligible.length;
    if (!eligible.length) {
      setNotice(`所选记录中没有可${action.label}的项目。`);
      return;
    }
    if (action.preview) {
      setPreview({ actionName, eligible, skipped });
      return;
    }
    if (window.confirm(action.confirm(eligible.length, skipped))) {
      void execute(actionName, eligible, skipped);
    }
  }

  async function execute(actionName, eligible, skipped) {
    if (busy || loading) return;
    const action = actions[actionName];
    setPreview(null);
    setBusy(actionName);
    setNotice("");
    setProgress(`正在${action.label}：0/${eligible.length}`);
    try {
      if (action.batchAction) {
        const state = await executeRecordedMonitoringBatch(eligible, action, (state) => {
          onTaskChange?.(action.batchAction, state);
          setProgress(`${action.batchAction === "followup" ? "总追问数" : "总删除数"} ${state.counts.total} / 当前已完成数 ${state.counts.completed}`);
        }, { start: startMonitoringBatch, read: getMonitoringBatch,
          wait: () => new Promise((resolve) => window.setTimeout(resolve, 1000)) });
        setNotice(`${state.message}${skipped ? `另有 ${skipped} 条不符合条件，未提交。` : ""}`);
      } else {
        const { success, processed, failures } = await executeMonitoringBatch(eligible, action,
          (done, total) => setProgress(`正在${action.label}：${done}/${total}`));
        const skippedTotal = skipped + eligible.length - processed;
        setNotice(`${action.label}完成：成功 ${success}，跳过 ${skippedTotal}，失败 ${failures.length}。${failures.slice(0, 2).join("；")}`);
        playConfirmationAlert();
      }
      setSelected(new Map());
      await onReload();
    } catch (cause) {
      setNotice(cause.message || "批量任务请求失败，列表会自动更新状态。");
      await onReload();
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  return { batchMode, setBatchMode, selectedKeys, selectedItems, allSelected,
    busy, disabled: Boolean(busy || loading), progress, notice, preview, setPreview, toggle, selectAll, close, prepare, execute };
}
