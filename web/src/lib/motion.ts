import { useEffect, useRef, useState } from "react";

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

/**
 * Counts from 0 (or the last shown value) up to `target` over `durationMs`.
 * Under prefers-reduced-motion it returns `target` straight away.
 */
export function useCountUp(target: number, durationMs = 600): number {
  const [reduced] = useState(prefersReducedMotion);
  const [shown, setShown] = useState(0);
  const shownRef = useRef(0);

  useEffect(() => {
    if (reduced) return;
    const from = shownRef.current;
    let frame = 0;
    let startedAt: number | undefined;
    const step = (now: number) => {
      startedAt ??= now;
      const t = Math.min((now - startedAt) / durationMs, 1);
      const value = t >= 1 ? target : Math.round(from + (target - from) * easeOutCubic(t));
      shownRef.current = value;
      setShown(value);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs, reduced]);

  return reduced ? target : shown;
}
