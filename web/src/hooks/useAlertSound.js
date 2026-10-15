import { useCallback, useEffect, useRef } from "react";

// Play pi-web's softer two-note chime twice for each completed task.
function playConfirmationPattern(context) {
  const start = context.currentTime;
  for (const repeatOffset of [0, 0.4]) {
    for (const [index, frequency] of [523.25, 659.25].entries()) {
      const noteStart = start + repeatOffset + index * 0.18;
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, noteStart);
      gain.gain.linearRampToValueAtTime(0.18, noteStart + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, noteStart + 0.42);
      oscillator.start(noteStart);
      oscillator.stop(noteStart + 0.42);
      oscillator.onended = () => {
        oscillator.disconnect();
        gain.disconnect();
      };
    }
  }
}

export function useAlertSound() {
  const contextRef = useRef(null);
  const resumePendingRef = useRef(false);

  const getContext = useCallback(() => {
    if (contextRef.current && contextRef.current.state !== "closed") return contextRef.current;
    try {
      contextRef.current = new AudioContext();
    } catch {
      return null;
    }
    return contextRef.current;
  }, []);

  const unlockAlertSound = useCallback(() => {
    const context = getContext();
    if (context?.state === "suspended") void context.resume().catch(() => {});
  }, [getContext]);

  const playConfirmationAlert = useCallback(() => {
    const context = getContext();
    if (!context) return;
    const play = () => {
      try {
        playConfirmationPattern(context);
      } catch {
        // The visual task state still works if audio is unavailable.
      }
    };
    if (context.state === "suspended") {
      if (resumePendingRef.current) return;
      resumePendingRef.current = true;
      void context.resume().then(play).catch(() => {}).finally(() => {
        resumePendingRef.current = false;
      });
      return;
    }
    play();
  }, [getContext]);

  useEffect(() => {
    document.addEventListener("pointerdown", unlockAlertSound);
    document.addEventListener("keydown", unlockAlertSound);
    return () => {
      document.removeEventListener("pointerdown", unlockAlertSound);
      document.removeEventListener("keydown", unlockAlertSound);
    };
  }, [unlockAlertSound]);

  useEffect(() => () => {
    const context = contextRef.current;
    if (context && context.state !== "closed") void context.close().catch(() => {});
  }, []);

  return { playConfirmationAlert };
}
