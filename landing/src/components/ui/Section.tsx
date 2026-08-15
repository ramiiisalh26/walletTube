import type { PropsWithChildren } from "react";
import { Container } from "./Container";

interface SectionProps {
  id?: string;
  className?: string;
}

/** Vertical rhythm wrapper for a page section. */
export function Section({
  id,
  className = "",
  children,
}: PropsWithChildren<SectionProps>) {
  return (
    <section id={id} className={`py-20 sm:py-28 ${className}`}>
      <Container>{children}</Container>
    </section>
  );
}
