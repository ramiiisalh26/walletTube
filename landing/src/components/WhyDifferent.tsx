import { Check, Minus } from "lucide-react";
import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { whyDifferent } from "../lib/copy";

export function WhyDifferent() {
  return (
    <Section id="why-different">
      <SectionHeading
        eyebrow="Why it's different"
        title={whyDifferent.heading}
        subtitle={whyDifferent.subhead}
      />

      <div className="mt-14 grid grid-cols-1 gap-4 md:grid-cols-3">
        {whyDifferent.columns.map((col, i) => {
          const isAccent = col.tone === "accent";
          return (
            <Reveal key={col.title} delay={i * 0.1}>
              <div
                className={`h-full rounded-2xl border p-6 ${
                  isAccent
                    ? "border-accent/50 bg-accent/[0.07] shadow-glow"
                    : "border-line bg-surface/40"
                }`}
              >
                <h3
                  className={`font-display text-lg font-semibold ${
                    isAccent ? "text-white" : "text-zinc-300"
                  }`}
                >
                  {col.title}
                </h3>
                <ul className="mt-4 space-y-3">
                  {col.points.map((point) => (
                    <li key={point} className="flex items-start gap-2.5 text-sm">
                      {isAccent ? (
                        <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent-300" />
                      ) : (
                        <Minus className="mt-0.5 h-4 w-4 shrink-0 text-zinc-600" />
                      )}
                      <span
                        className={isAccent ? "text-zinc-200" : "text-zinc-500"}
                      >
                        {point}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </Reveal>
          );
        })}
      </div>
    </Section>
  );
}
