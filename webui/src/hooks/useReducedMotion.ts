import { useEffect, useState } from "react";

/**
 * Reactive ``prefers-reduced-motion`` listener. Returns ``true`` when the OS
 * setting is "reduce". Used to disable JS-driven animations (e.g. Streamdown's
 * per-word fade-in) since those bypass Tailwind's ``motion-safe:`` CSS gate.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return reduced;
}
