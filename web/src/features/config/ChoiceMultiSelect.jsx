import { useEffect, useRef, useState } from "react";

export default function ChoiceMultiSelect({ id, label, path, options, values, onChange,
  emptyText, help, optionsClassName = "" }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const labels = new Map(options);
  const selectedLabels = values.map((value) => labels.get(value) || value);

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

  function toggle(value) {
    onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  }

  return <div className="config-field config-city-field" ref={root}>
    <label htmlFor={id}>{label}</label>
    <div className="config-city-picker">
      <div className={`config-city-control${open ? " is-open" : ""}`}>
        <button id={id} type="button" className="config-city-opener"
          aria-expanded={open} aria-controls={`${id}-panel`}
          aria-label={values.length ? `${label}，已选择${selectedLabels.join("、")}` : `${label}，选择${label}`}
          onClick={() => setOpen((current) => !current)}>
          {values.length === 0 && emptyText}
        </button>
        {values.length > 0 && <div className="config-city-tags" aria-label={`已选${label}`}>
          {values.map((value) => <span className="config-city-tag" key={value}>
            {labels.get(value) || value}
            <button type="button" onClick={() => toggle(value)}
              aria-label={`移除${labels.get(value) || value}`}>×</button>
          </span>)}
        </div>}
        <span className="config-city-chevron" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </div>
      {open && <div className="config-city-panel" id={`${id}-panel`}>
        <div className={`config-city-options${optionsClassName ? ` ${optionsClassName}` : ""}`}>
          {options.map(([value, text]) => <label key={value}>
            <input type="checkbox" checked={values.includes(value)} onChange={() => toggle(value)} />
            <span>{text}</span>
          </label>)}
        </div>
      </div>}
    </div>
    <code>{path}</code>
    {help && <p>{help}</p>}
  </div>;
}
