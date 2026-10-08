export default function WorkspaceSidebar({ activeView, onViewChange }) {
  return (
    <aside className="workspace-sidebar" aria-label="主导航">
      <div className="sidebar-brand">
        <span className="brand-mark">J</span>
        <span>Job Hunting Helper</span>
      </div>
      <nav className="sidebar-nav" aria-label="工作台导航">
        <button
          type="button"
          className={`sidebar-parent${activeView === "workbench" ? " active" : ""}`}
          onClick={() => onViewChange("workbench")}
          aria-expanded="true"
          aria-current={activeView === "workbench" ? "page" : undefined}
        >
          工作台
        </button>
        <div className="sidebar-children">
          <button type="button" className={`sidebar-child${activeView === "collection" ? " active" : ""}`}
            aria-current={activeView === "collection" ? "page" : undefined}
            onClick={() => onViewChange("collection")}>采集任务</button>
          <button type="button" className={`sidebar-child${activeView === "monitoring" ? " active" : ""}`}
            aria-current={activeView === "monitoring" ? "page" : undefined}
            onClick={() => onViewChange("monitoring")}>监测任务</button>
        </div>
        <button type="button" className={`sidebar-parent sidebar-config${activeView === "config" ? " active" : ""}`}
          aria-current={activeView === "config" ? "page" : undefined}
          onClick={() => onViewChange("config")}>
          配置
        </button>
      </nav>
    </aside>
  );
}
