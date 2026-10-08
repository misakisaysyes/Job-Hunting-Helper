import { useState } from "react";
import CollectionTimelinePreview from "./CollectionTimelinePreview";
import MonitoringBatchPreview from "./MonitoringBatchPreview";

export default function BatchWorkflowPreview({ settings, preview }) {
  const [visible, setVisible] = useState(false);
  const ready = Boolean(settings && preview);

  return <section className="workbench-batch-preview" aria-labelledby="batch-preview-title">
    <div className="workbench-batch-preview-header">
      <div>
        <h2 id="batch-preview-title">批量任务工作流预览</h2>
        <p>采集按岗位容量和时间展示；监测按一轮会话扫描展示。</p>
      </div>
      <div className="workbench-batch-preview-actions">
        <button type="button" onClick={() => setVisible(true)} disabled={!ready}>生成预览图</button>
      </div>
    </div>
    {visible && ready && <>
      <CollectionTimelinePreview settings={settings} preview={preview} />
      <MonitoringBatchPreview settings={settings} />
      <p className="workbench-batch-preview-hint">配置保存后，预览图使用最新值更新；预览不会启动任务。</p>
    </>}
  </section>;
}
