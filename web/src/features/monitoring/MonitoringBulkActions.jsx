import { useCloseOnOutsideBlank } from "../../hooks/useCloseOnOutsideBlank";

export default function MonitoringBulkActions({ label, count, actions, batch, onClose }) {
  const panelRef = useCloseOnOutsideBlank(onClose, Boolean(batch.busy));
  return <aside ref={panelRef} className="bulk-actions" aria-label={`${label}批量操作`}>
    <strong>已选择 {count} 条{label}</strong>
    {batch.progress && <span role="status">{batch.progress}</span>}
    {batch.notice && <p role="status">{batch.notice}</p>}
    {actions.map(({ key, label: actionLabel, danger }) =>
      <button key={key} type="button" className={danger ? "bulk-danger" : ""}
        disabled={!count || batch.disabled} onClick={() => batch.prepare(key)}>{actionLabel}</button>)}
    <button type="button" disabled={Boolean(batch.busy)} onClick={onClose}>关闭批量操作</button>
  </aside>;
}
