import { useEffect, useState } from "react";
import { applyFullSettings, getFullSettings, saveFullSettings } from "../../api/settings";
import { FIELD_HELP, FIELD_LABELS, FIELD_OPTIONS, RESTART_FIELDS } from "./configFields";
import { CONFIG_SECTIONS } from "./configLayout";
import TagInput from "../../components/TagInput";
import ResumeUploadField from "./ResumeUploadField";
import CityMultiSelect from "./CityMultiSelect";
import FollowupTypeMultiSelect from "./FollowupTypeMultiSelect";
import "./config.css";

const RECRUITMENT_LABELS = { experienced: "社招", campus: "校招", internship: "实习" };

async function waitForApiRestart(previousInstanceId) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      const result = await getFullSettings();
      if (result.server_instance_id && result.server_instance_id !== previousInstanceId) {
        return result;
      }
    } catch {
      // The API is briefly unavailable while its process restarts.
    }
  }
  throw new Error("API 服务仍在重启，请稍后刷新页面查看状态");
}

function toDraft(settings, cityOptions = []) {
  const draft = Object.fromEntries(Object.entries(settings).map(([section, fields]) => [
    section,
    Object.fromEntries(Object.entries(fields).map(([key, value]) => [
      key, Array.isArray(value) ? [...value] : typeof value === "boolean" ? value : String(value),
    ])),
  ]));
  if (!draft.collection.cities.length && cityOptions.includes(draft.collection.city)) {
    draft.collection.cities = [draft.collection.city];
  }
  return draft;
}

function toPayload(draft, settings) {
  const payload = Object.fromEntries(Object.entries(draft).map(([section, fields]) => [
    section,
    Object.fromEntries(Object.entries(fields).map(([key, value]) => {
      const original = settings[section][key];
      if (Array.isArray(original)) return [key, value];
      if (typeof original === "number") {
        if (String(value).trim() === "") throw new Error(`${section}.${key} 不能为空`);
        const number = Number(value);
        if (!Number.isFinite(number)) throw new Error(`${section}.${key} 必须是数字`);
        return [key, number];
      }
      return [key, value];
    })),
  ]));
  if (payload.collection.mode === "recommend") {
    payload.collection.keywords = [];
    payload.collection.cities = [];
    payload.collection.city = "";
    payload.collection.city_code = "";
  }
  if (payload.collection.mode === "search") {
    payload.collection.encrypt_expect_id = [];
    payload.collection.city = "";
    payload.collection.city_code = "";
  }
  payload.collection.max_jobs = 0;
  delete payload.ai.api_key_env;
  delete payload.collection.test_prefilter;
  delete payload.browser;
  delete payload.safety.state_db;
  return payload;
}

function sectionFieldCount(section, draft) {
  return section.groups.reduce((count, group) =>
    count + (section.key === "ai" && group.key === "decisions"
      ? 3 + (draft.ai.use_ai_score ? 2 : 0)
      : group.fields.length + (group.branches?.[draft.collection.mode]?.fields.length || 0)), 0);
}

function ConfigField({ section, name, original, value, onChange, disabled = false }) {
  const id = `config-${section}-${name}`;
  const key = `${section}.${name}`;
  const label = FIELD_LABELS[section]?.[name] || name;
  const help = FIELD_HELP[key];
  const options = FIELD_OPTIONS[key];
  const common = { id, name: key };
  let control;
  if (typeof original === "boolean") {
    control = <input {...common} type="checkbox" checked={Boolean(value)} disabled={disabled}
      onChange={(event) => onChange(event.target.checked)} />;
  } else if (options) {
    control = <select {...common} value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map(([option, text]) => <option key={option} value={option}>{text}</option>)}
    </select>;
  } else if (Array.isArray(original)) {
    control = <TagInput {...common} values={value} onChange={onChange}
      labels={key === "profile.recruitment_types" ? RECRUITMENT_LABELS : undefined} />;
  } else if (name === "score_user_prompt" || name === "greeting_user_prompt"
      || name === "greeting_template") {
    control = <textarea {...common} rows={3} value={value}
      maxLength={name === "greeting_template" ? 300 : undefined}
      onChange={(event) => onChange(event.target.value)} />;
  } else if (typeof original === "number") {
    control = <input {...common} type="number" step={Number.isInteger(original) ? "1" : "any"}
      value={value} onChange={(event) => onChange(event.target.value)} />;
  } else {
    control = <input {...common} type="text" value={value}
      onChange={(event) => onChange(event.target.value)} />;
  }
  return <div className={`config-field ${typeof original === "boolean" ? "config-toggle" : ""}`}>
    <label htmlFor={id}>{label} {RESTART_FIELDS.has(key) && <em>生效时自动重启</em>}</label>
    {control}
    <code>{key}</code>
    {help && <p>{help}</p>}
  </div>;
}

export default function ConfigurationPage() {
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState(null);
  const [cityOptions, setCityOptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [pendingApply, setPendingApply] = useState(false);
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [uploadingResume, setUploadingResume] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState(null);
  const [openSections, setOpenSections] = useState(() => new Set(["collection", "safety"]));

  useEffect(() => {
    let active = true;
    getFullSettings().then((result) => {
      if (!active) return;
      setSettings(result.settings);
      setCityOptions(result.city_options || []);
      setDraft(toDraft(result.settings, result.city_options || []));
      setPendingApply(Boolean(result.pending_apply));
      setApiKeyConfigured(Boolean(result.api_key_configured));
      setNotice(null);
    }).catch((cause) => {
      if (active) setNotice({ type: "error", text: cause.message || "无法读取配置" });
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function update(section, key, value) {
    setDraft((current) => {
      const nextValue = typeof value === "function" ? value(current[section][key]) : value;
      const next = { ...current, [section]: { ...current[section], [key]: nextValue } };
      if (section === "ai" && key === "use_ai_score" && !nextValue) {
        next.ai.use_ai_greeting = false;
      }
      return next;
    });
    setDirty(true);
    setNotice(null);
  }

  async function persistSettings() {
    const payload = toPayload(draft, settings);
    if (apiKeyInput.trim()) payload.ai.api_key = apiKeyInput.trim();
    const result = await saveFullSettings(payload);
    setSettings(result.settings);
    setCityOptions(result.city_options || cityOptions);
    setDraft(toDraft(result.settings, result.city_options || cityOptions));
    setDirty(false);
    setPendingApply(Boolean(result.pending_apply));
    setApiKeyConfigured(Boolean(result.api_key_configured));
    setApiKeyInput("");
    return result;
  }

  async function save(event) {
    event.preventDefault();
    if (uploadingResume || applying) return;
    setSaving(true);
    setNotice(null);
    try {
      await persistSettings();
      setNotice({ type: "success", text: "配置已保存。点击「生效配置」后，下一轮任务将使用新配置。" });
    } catch (cause) {
      setNotice({ type: "error", text: cause.message || "保存配置失败" });
    } finally {
      setSaving(false);
    }
  }

  async function apply() {
    if (saving || uploadingResume || applying) return;
    setApplying(true);
    setNotice(null);
    try {
      if (dirty) await persistSettings();
      const result = await applyFullSettings();
      if (result.restarting) {
        setNotice({ type: "success", text: "配置已保存，正在重启 API 服务…" });
        const ready = await waitForApiRestart(result.server_instance_id);
        setPendingApply(Boolean(ready.pending_apply));
        setApiKeyConfigured(Boolean(ready.api_key_configured));
        setNotice({ type: "success", text: "配置已生效，API 服务已自动重启。下一轮任务将使用新配置。" });
      } else {
        setPendingApply(Boolean(result.pending_apply));
        setNotice({ type: "success", text: "配置已生效。下一轮任务将使用新配置。" });
      }
    } catch (cause) {
      setNotice({ type: "error", text: cause.message || "生效配置失败" });
    } finally {
      setApplying(false);
    }
  }

  function resumeUploaded(path) {
    setSettings((current) => ({ ...current, profile: { ...current.profile, resume_path: path } }));
    setDraft((current) => ({ ...current, profile: { ...current.profile, resume_path: path } }));
  }

  function renderField(path) {
    const [section, name] = path.split(".");
    if (path === "monitoring.followup_types") {
      const values = [draft.monitoring.followup_unread && "unread",
        draft.monitoring.followup_read_no_reply && "read_no_reply"].filter(Boolean);
      return <FollowupTypeMultiSelect key={path} values={values} onChange={(selected) => {
        setDraft((current) => ({ ...current, monitoring: { ...current.monitoring,
          followup_unread: selected.includes("unread"),
          followup_read_no_reply: selected.includes("read_no_reply"),
        } }));
        setDirty(true);
        setNotice(null);
      }} />;
    }
    if (path === "ai.api_key") {
      return <div className="config-field config-secret-field" key={path}>
        <label htmlFor="config-ai-api-key">API Key</label>
        <input id="config-ai-api-key" name="ai.api_key" type="password" autoComplete="new-password"
          value={apiKeyInput} placeholder={apiKeyConfigured ? "已保存；输入新 Key 可替换" : "输入 API Key"}
          onChange={(event) => { setApiKeyInput(event.target.value); setDirty(true); setNotice(null); }} />
        <p>{apiKeyConfigured ? "本地已保存 Key，页面不会显示原文。留空表示保持原值。"
          : "填写后保存并点击生效配置；也可以继续使用环境变量。"}</p>
      </div>;
    }
    const original = settings[section][name];
    if (path === "collection.cities") {
      return <CityMultiSelect key={path} options={cityOptions} values={draft.collection.cities}
        onChange={(value) => update(section, name, value)} />;
    }
    return section === "profile" && name === "resume_path"
      ? <ResumeUploadField key={path} currentPath={draft.profile.resume_path}
          onUploaded={resumeUploaded} onUploadingChange={setUploadingResume} />
      : <ConfigField key={path} section={section} name={name} original={original}
          value={draft[section][name]}
          disabled={path === "ai.use_ai_greeting" && !draft.ai.use_ai_score}
          onChange={(value) => update(section, name, value)} />;
  }

  function sectionToggled(key, isOpen) {
    setOpenSections((current) => {
      if (current.has(key) === isOpen) return current;
      const next = new Set(current);
      if (isOpen) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  return <main className="content config-page">
    <header className="config-header">
      <p className="eyebrow">SETTINGS</p>
      <h1>配置</h1>
    </header>
    {loading ? <p>正在读取配置…</p> : !draft ? null : <form onSubmit={save}>
      <div className="config-actions">
        <div className="config-actions-copy">
          <strong>{dirty ? "有未保存的修改" : pendingApply ? "已保存，待生效" : "所有修改已生效"}</strong>
          <span>配置采集、监测和 AI 处理流程。保存时会检查配置约束并写入本地；点击生效后用于下一轮任务。需要重启时，系统会自动重启 API 服务。</span>
          {notice && <p className={`config-action-notice ${notice.type}`} role="status">{notice.text}</p>}
        </div>
        <div className="config-actions-buttons">
          <button type="submit" className="start-button" disabled={!dirty || saving || applying || uploadingResume}>
            {saving ? "保存中…" : "保存配置"}
          </button>
          <button type="button" className="start-button config-apply-button"
            disabled={saving || applying || uploadingResume} onClick={apply}>
            {applying ? "生效中…" : "生效配置"}
          </button>
        </div>
      </div>
      {CONFIG_SECTIONS.map((section) => <details className="config-section" key={section.key}
        open={openSections.has(section.key)}
        onToggle={(event) => sectionToggled(section.key, event.currentTarget.open)}>
        <summary><span>{section.title}</span><small>{sectionFieldCount(section, draft)} 项</small></summary>
        <p className="config-section-description">{section.description}</p>
        <div className="config-modules">
          {section.groups.map((group) => {
            const branch = group.branches?.[draft.collection.mode];
            return <section className={`config-module${branch ? " config-module-source" : ""}`} key={group.key}>
              <div className="config-module-heading">
                <h3>{group.title}</h3>
                {group.description && <p>{group.description}</p>}
              </div>
              {section.key === "ai" && group.key === "decisions"
                ? <div className="config-decision-grid">
                  <div className="config-decision-card">
                    <h4>岗位评分</h4>
                    <div className="config-fields">
                      {renderField("ai.use_ai_score")}
                      {draft.ai.use_ai_score && <>
                        {renderField("ai.score_threshold")}
                        {renderField("ai.score_user_prompt")}
                      </>}
                    </div>
                  </div>
                  <div className="config-decision-card">
                    <h4>招呼语</h4>
                    <div className="config-fields">
                      {renderField("ai.use_ai_greeting")}
                      {renderField(draft.ai.use_ai_greeting
                        ? "ai.greeting_user_prompt" : "ai.greeting_template")}
                    </div>
                  </div>
                </div>
                : <div className="config-fields">{group.fields.map(renderField)}</div>}
              {branch && <div className={`config-branch config-branch-${draft.collection.mode}`}>
                <div className="config-module-heading">
                  <h4>{branch.title}需要配置</h4>
                  <p>{branch.description}</p>
                </div>
                <div className="config-fields">{branch.fields.map(renderField)}</div>
              </div>}
            </section>;
          })}
        </div>
      </details>)}
    </form>}
  </main>;
}
