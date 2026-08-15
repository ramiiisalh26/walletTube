import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { problem } from "../lib/copy";

export function Problem() {
  return (
    <Section id="problem">
      <SectionHeading title={problem.heading} subtitle={problem.body} />

      <ul className="mt-14 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {problem.personas.map((p, i) => (
          <Reveal as="li" key={p.label} delay={i * 0.08}>
            <div className="h-full rounded-2xl border border-line bg-surface/60 p-6 transition-colors hover:border-accent/40">
              <span className="text-3xl" aria-hidden="true">
                {p.emoji}
              </span>
              <p className="mt-4 text-sm font-semibold text-white">{p.label}</p>
              <p className="mt-1 text-sm text-zinc-400">{p.line}</p>
            </div>
          </Reveal>
        ))}
      </ul>
    </Section>
  );
}
