import { useRef, useState } from "react";
import "./TagInput.css";

export default function TagInput({ id, name, values, onChange, labels = {} }) {
  const [pending, setPending] = useState("");
  const composing = useRef(false);

  function add(text) {
    const additions = text.split(/\s+/).map((item) => item.trim()).filter(Boolean)
      .map((item) => Object.entries(labels).find(([key, label]) =>
        item === label || item.toLowerCase() === key.toLowerCase())?.[0] || item);
    if (additions.length) onChange((current) => [...new Set([...current, ...additions])]);
    setPending("");
  }

  function change(event) {
    const text = event.target.value;
    if (composing.current) {
      setPending(text);
      return;
    }
    if (/\s/.test(text)) add(text);
    else setPending(text);
  }

  function keyDown(event) {
    if (composing.current || event.nativeEvent.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      add(pending);
    } else if (event.key === "Backspace" && !pending && values.length) {
      onChange((current) => current.slice(0, -1));
    }
  }

  return <div className="tag-input" onClick={(event) => {
    if (event.target === event.currentTarget) event.currentTarget.querySelector("input")?.focus();
  }}>
    {values.map((value, index) => <span className="tag-input-chip" key={`${value}-${index}`}>
      <span>{labels[value] || value}</span>
      <button type="button" aria-label={`删除 ${labels[value] || value}`}
        onClick={() => onChange((current) => current.filter((_, item) => item !== index))}>×</button>
    </span>)}
    <input id={id} name={name} type="text" value={pending}
      placeholder={values.length ? "" : "输入后按空格添加"}
      onChange={change} onKeyDown={keyDown}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={(event) => { composing.current = false; change(event); }}
      onBlur={() => pending && add(pending)} />
  </div>;
}
