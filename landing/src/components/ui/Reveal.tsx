import type { PropsWithChildren } from "react";
import { motion, useReducedMotion } from "framer-motion";

interface RevealProps {
  delay?: number;
  className?: string;
  /** Tag to render as (defaults to div). */
  as?: "div" | "li" | "section";
}

/**
 * Scroll-triggered fade+rise. Renders the final state immediately when the
 * user prefers reduced motion.
 */
export function Reveal({
  children,
  delay = 0,
  className = "",
  as = "div",
}: PropsWithChildren<RevealProps>) {
  const reduce = useReducedMotion();
  const MotionTag = motion[as];

  if (reduce) {
    const Tag = as;
    return <Tag className={className}>{children}</Tag>;
  }

  return (
    <MotionTag
      className={className}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </MotionTag>
  );
}
