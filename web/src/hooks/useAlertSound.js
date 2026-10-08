import { useCallback, useEffect, useRef } from "react";

// Match BossHunter's two-round Web Audio completion chime.
function playConfirmationPattern(context) {
  const start = context.currentTime;
  const frequencies = [523.25, 659.25];
  for (let round = 0; round < 2; round += 1) {
    frequencies.forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      const time = start + round * 0.4 + index * 0.18;
      gain.gain.setValueAtTime(0, time);
      gain.gain.linearRampToValueAtTime(0.28, time + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, time + 0.45);
      oscillator.start(time);
      oscillator.stop(time + 0.45);
    });
  }
}

export function useAlertSound() {
  const contextRef = useRef(null);

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
      void context.resume().then(play).catch(() => {});
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
