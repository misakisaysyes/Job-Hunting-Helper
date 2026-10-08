import { useEffect, useRef } from "react";

const INTERACTIVE = "button, input, select, textarea, a, label, [role='button'], [role='dialog'], .followup-modal-backdrop";

export function useCloseOnOutsideBlank(onClose, disabled = false) {
  const panelRef = useRef(null);

  useEffect(() => {
    function handlePointerDown(event) {
      if (disabled || !(event.target instanceof Element)) return;
      if (panelRef.current?.contains(event.target)) return;
      if (event.target.closest(INTERACTIVE)) return;
      onClose();
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [disabled, onClose]);

  return panelRef;
}
