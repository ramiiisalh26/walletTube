import { brand } from "../../lib/copy";

/**
 * Placeholder logo + wordmark. Swap the SVG mark or the text freely — the mark
 * is a "moment" dot converging inside a search ring.
 */
export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <a
      href="#top"
      className={`flex items-center gap-2 font-display text-lg font-bold tracking-tight text-white ${className}`}
      aria-label={`${brand.name} — home`}
    >
      <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden="true">
        <circle cx="16" cy="16" r="13" fill="none" stroke="#8b5cf6" strokeOpacity="0.4" strokeWidth="2" />
        <circle cx="16" cy="16" r="5" fill="#8b5cf6" />
      </svg>
      {brand.name}
    </a>
  );
}
