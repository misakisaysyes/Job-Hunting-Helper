import { useEffect, useState } from "react";
import { executeMonitoringBatch } from "./monitoringBatch";

export const monitoringKey = (item) => `${item.platform}:${item.conversation_id}`;

export default function useMonitoringBatch({ items, page, filters, actions, onReload, loading }) {
  const [batchMode, setBatchMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState(new Set());
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState(null);
  const selectedItems = items.filter((item) => selectedKeys.has(monitoringKey(item)));
  const allSelected = items.length > 0 && selectedItems.length === items.length;

  useEffect(() => { setSelectedKeys(new Set()); setNotice(""); }, [page, filters]);

  function toggle(item) {
    setSelectedKeys((previous) => {
      const next = new Set(previous);
      const key = monitoringKey(item);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectAll(checked) {
    setSelectedKeys(checked ? new Set(items.map(monitoringKey)) : new Set());
  }

  function close() {
    if (busy) return;
    setBatchMode(false);
    setSelectedKeys(new Set());
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
      const { success, processed, failures } = await executeMonitoringBatch(eligible, action,
        (done, total) => setProgress(`正在${action.label}：${done}/${total}`));
      const skippedTotal = skipped + eligible.length - processed;
      setNotice(`${action.label}完成：成功 ${success}，跳过 ${skippedTotal}，失败 ${failures.length}。${failures.slice(0, 2).join("；")}`);
      setSelectedKeys(new Set());
      onReload();
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  return { batchMode, setBatchMode, selectedKeys, selectedItems, allSelected,
    busy, progress, notice, preview, setPreview, toggle, selectAll, close, prepare, execute };
}
