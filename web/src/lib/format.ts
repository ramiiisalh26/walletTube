/** 95 → "1:35", 3725 → "1:02:05" */
export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** 1500 → "1.5K", 2_300_000 → "2.3M" */
export function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Clamp a 0–1 similarity into a 0–100 integer for display. */
export function relevancePercent(similarity: number): number {
  return Math.round(Math.min(1, Math.max(0, similarity)) * 100);
}
