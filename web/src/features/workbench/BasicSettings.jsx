import { useEffect, useState } from "react";

const splitValues = (value) => value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);

export function CollectionBasics({ settings, checks, onSave }) {
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (!settings) return;
    setDraft({
      mode: settings.collection.mode,
      expectIds: settings.collection.encrypt_expect_id.join(", "),
      keywords: settings.collection.keywords.join(", "),
      cdpUrl: settings.browser.cdp_url,
      resumePath: settings.profile.resume_path,
      useAiScore: settings.ai.use_ai_score,
    });
  }, [settings]);

  if (!draft) return <p className="workbench-settings-loading">正在读取采集配置…</p>;

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      await onSave({
        collection: {
          mode: draft.mode,
          encrypt_expect_id: draft.mode === "recommend" ? splitValues(draft.expectIds) : [],
          keywords: draft.mode === "search" ? splitValues(draft.keywords) : [],
        },
        browser: { cdp_url: draft.cdpUrl },
        profile: { resume_path: draft.resumePath },
        ai: {
          use_ai_score: draft.useAiScore,
          use_ai_greeting: draft.useAiScore && settings.ai.use_ai_greeting,
        },
      });
      setNotice({ type: "success", text: "采集配置已保存，下轮任务生效。" });
    } catch (error) {
      setNotice({ type: "error", text: error.message || "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  const update = (key, value) => setDraft((current) => ({ ...current, [key]: value }));
  return <form className="workbench-settings" onSubmit={save}>
    <h3>运行前配置</h3>
    <div className="workbench-field-row">
      <label>采集来源
        <select value={draft.mode} onChange={(event) => update("mode", event.target.value)}>
          <option value="recommend">推荐流</option><option value="search">搜索流</option>
        </select>
      </label>
      <label>Chrome 调试地址
        <input value={draft.cdpUrl} onChange={(event) => update("cdpUrl", event.target.value)} />
      </label>
    </div>
    {draft.mode === "recommend" ? <label>求职期望 ID（多个用逗号分隔）
      <input value={draft.expectIds} onChange={(event) => update("expectIds", event.target.value)} />
    </label> : <label>搜索关键词（多个用逗号分隔）
      <input value={draft.keywords} onChange={(event) => update("keywords", event.target.value)} />
    </label>}
    <label className="workbench-checkbox">
      <input type="checkbox" checked={draft.useAiScore}
        onChange={(event) => update("useAiScore", event.target.checked)} />
      启用 AI 评分
    </label>
    {draft.useAiScore && <>
      <label>简历文件路径
        <input value={draft.resumePath} onChange={(event) => update("resumePath", event.target.value)} />
      </label>
      <p className="workbench-checks">
        简历文件：{checks?.resume_exists ? "已找到" : "未找到"} · API Key：{checks?.api_key_ready ? "已设置" : "未设置，可在配置页填写或使用环境变量"}
      </p>
    </>}
    <div className="workbench-settings-footer">
      <span>保存后用于下轮采集；已运行的任务不受影响。</span>
      <button type="submit" disabled={saving}>{saving ? "保存中…" : "保存采集配置"}</button>
    </div>
    {notice && <p className={`workbench-settings-notice ${notice.type}`} role="status">{notice.text}</p>}
  </form>;
}

export function MonitoringBasics({ settings, onSave }) {
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    if (!settings) return;
    setDraft({ ...settings.monitoring });
  }, [settings]);

  if (!draft) return <p className="workbench-settings-loading">正在读取监测配置…</p>;

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      await onSave({ monitoring: {
        message_limit: Number(draft.message_limit),
      } });
      setNotice({ type: "success", text: "监测配置已保存，下轮任务生效。" });
    } catch (error) {
      setNotice({ type: "error", text: error.message || "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  return <form className="workbench-settings" onSubmit={save}>
    <h3>运行前配置</h3>
    <div className="workbench-field-row">
      <label>每条会话读取的消息数
        <input type="number" min="1" max="1000" value={draft.message_limit}
          onChange={(event) => setDraft((current) => ({ ...current, message_limit: event.target.value }))} />
      </label>
    </div>
    <div className="workbench-settings-footer">
      <span>保存后用于下轮监测；已运行的任务不受影响。</span>
      <button type="submit" disabled={saving}>{saving ? "保存中…" : "保存监测配置"}</button>
    </div>
    {notice && <p className={`workbench-settings-notice ${notice.type}`} role="status">{notice.text}</p>}
  </form>;
}
