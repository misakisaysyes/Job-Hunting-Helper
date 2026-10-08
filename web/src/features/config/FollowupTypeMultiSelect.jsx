import { useEffect, useRef, useState } from "react";
import { FIELD_HELP } from "./configFields";

const OPTIONS = [
  { key: "unread", label: "未读" },
  { key: "read_no_reply", label: "已读未回" },
];

export default function FollowupTypeMultiSelect({ values, onChange }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const id = "config-monitoring-followup-types";

  useEffect(() => {
    if (!open) return undefined;
    function closeOnOutside(event) {
      if (!root.current?.contains(event.target)) setOpen(false);
    }
    function closeOnEscape(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function toggle(key) {
    onChange(values.includes(key) ? values.filter((value) => value !== key) : [...values, key]);
  }

  const chosen = OPTIONS.filter(({ key }) => values.includes(key));
  return <div className="config-field config-city-field config-followup-types" ref={root}>
    <label htmlFor={id}>追问招呼类型</label>
    <div className="config-city-picker">
      <div className={`config-city-control${open ? " is-open" : ""}`}>
        <button id={id} type="button" className="config-city-opener"
          aria-expanded={open} aria-controls={`${id}-panel`}
          aria-label={chosen.length ? `追问招呼类型，已选择${chosen.map(({ label }) => label).join("、")}` : "选择追问招呼类型"}
          onClick={() => setOpen((current) => !current)}>
          {chosen.length === 0 && "选择追问招呼类型"}
        </button>
        {chosen.length > 0 && <div className="config-city-tags" aria-label="已选追问招呼类型">
          {chosen.map(({ key, label }) => <span className="config-city-tag" key={key}>
            {label}
            <button type="button" onClick={() => toggle(key)} aria-label={`移除${label}`}>×</button>
          </span>)}
        </div>}
        <span className="config-city-chevron" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </div>
      {open && <div className="config-city-panel" id={`${id}-panel`}>
        <div className="config-city-options config-followup-options">
          {OPTIONS.map(({ key, label }) => <label key={key}>
            <input type="checkbox" checked={values.includes(key)} onChange={() => toggle(key)} />
            <span>{label}</span>
          </label>)}
        </div>
      </div>}
    </div>
    <code>monitoring.followup_unread / monitoring.followup_read_no_reply</code>
    <p>{FIELD_HELP["monitoring.followup_types"]}</p>
  </div>;
}
