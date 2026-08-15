import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";

/**
 * Scattered "moments" drifting inward to a single point — the hero metaphor.
 * Reduced-motion: renders a static glow only, no particles.
 */
export function ConvergingBackground() {
  const reduce = useReducedMotion();

  const particles = useMemo(
    () =>
      Array.from({ length: 18 }, (_, i) => {
        const angle = (i / 18) * Math.PI * 2;
        const distance = 180 + (i % 5) * 60;
        return {
          id: i,
          x: Math.cos(angle) * distance,
          y: Math.sin(angle) * distance,
          size: 3 + (i % 3),
          duration: 3.5 + (i % 4) * 0.6,
          delay: (i % 6) * 0.5,
        };
      }),
    [],
  );

  return (
    <div
      className="pointer-events-none absolute inset-0 overflow-hidden"
      aria-hidden="true"
    >
      {/* central glow */}
      <div className="absolute left-1/2 top-1/2 h-64 w-64 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/20 blur-[100px]" />

      {!reduce && (
        <div className="absolute left-1/2 top-1/2">
          {particles.map((p) => (
            <motion.span
              key={p.id}
              className="absolute rounded-full bg-accent-300"
              style={{ width: p.size, height: p.size }}
              initial={{ x: p.x, y: p.y, opacity: 0 }}
              animate={{
                x: [p.x, 0],
                y: [p.y, 0],
                opacity: [0, 0.9, 0],
                scale: [1, 0.4],
              }}
              transition={{
                duration: p.duration,
                delay: p.delay,
                repeat: Infinity,
                ease: "easeIn",
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
