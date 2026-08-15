import { useEffect, useState } from "react";

/**
 * Cycles 0..length-1 on an interval. Pass `enabled=false` (e.g. for
 * reduced-motion) to freeze on the first item.
 */
export function useRotatingIndex(
  length: number,
  intervalMs = 3000,
  enabled = true,
): number {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!enabled || length <= 1) return;
    const id = window.setInterval(
      () => setIndex((i) => (i + 1) % length),
      intervalMs,
    );
    return () => window.clearInterval(id);
  }, [length, intervalMs, enabled]);

  return index;
}
