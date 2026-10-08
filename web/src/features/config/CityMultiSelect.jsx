import { useEffect, useRef, useState } from "react";
import { FIELD_HELP } from "./configFields";

export default function CityMultiSelect({ options, values, onChange }) {
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const id = "config-collection-cities";

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

  function toggle(city) {
    onChange(values.includes(city) ? values.filter((item) => item !== city) : [...values, city]);
  }

  return <div className="config-field config-city-field" ref={root}>
    <label id={`${id}-label`} htmlFor={id}>城市</label>
    <div className="config-city-picker">
      <div className={`config-city-control${open ? " is-open" : ""}`}>
        <button id={id} type="button" className="config-city-opener"
          aria-expanded={open} aria-controls={`${id}-panel`}
          aria-label={values.length ? `城市，已选择${values.join("、")}` : "城市，选择城市"}
          onClick={() => setOpen((current) => !current)}>
          {values.length === 0 && "选择城市（不限）"}
        </button>
        {values.length > 0 && <div className="config-city-tags" aria-label="已选城市">
          {values.map((city) => <span className="config-city-tag" key={city}>
            {city}
            <button type="button" onClick={() => toggle(city)} aria-label={`移除${city}`}>×</button>
          </span>)}
        </div>}
        <span className="config-city-chevron" aria-hidden="true">{open ? "▴" : "▾"}</span>
      </div>
      {open && <div className="config-city-panel" id={`${id}-panel`}>
        <div className="config-city-options">
          {options.map((city) => <label key={city}>
            <input type="checkbox" checked={values.includes(city)} onChange={() => toggle(city)} />
            <span>{city}</span>
          </label>)}
        </div>
      </div>}
    </div>
    <code>collection.cities</code>
    <p>{FIELD_HELP["collection.cities"]}</p>
  </div>;
}
