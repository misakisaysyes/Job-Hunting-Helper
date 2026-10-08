import React from "react";
import { useCloseOnOutsideBlank } from "../../hooks/useCloseOnOutsideBlank";

export default function BulkActions({ count, busy, progress, notice, onAction, onClose }) {
  const panelRef = useCloseOnOutsideBlank(onClose, Boolean(busy));
  return <aside ref={panelRef} className="bulk-actions" aria-label="批量操作">
    <strong>已选择 {count} 个岗位</strong>
    {progress && <span role="status">{progress}</span>}
    {notice && <p role="status">{notice}</p>}
    <button type="button" disabled={!count || Boolean(busy)} onClick={() => onAction("delete")}>批量删除</button>
    <button type="button" disabled={!count || Boolean(busy)} onClick={() => onAction("score")}>批量评分</button>
    <button type="button" disabled={!count || Boolean(busy)} onClick={() => onAction("start")}>批量流转到打招呼</button>
    <button type="button" disabled={!count || Boolean(busy)} onClick={() => onAction("generate")}>批量生成招呼语</button>
    <button type="button" disabled={!count || Boolean(busy)} onClick={() => onAction("send")}>批量发送</button>
    <button type="button" disabled={Boolean(busy)} onClick={onClose}>关闭批量操作</button>
  </aside>;
}
