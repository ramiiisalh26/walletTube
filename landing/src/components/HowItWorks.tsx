import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { howItWorks } from "../lib/copy";

export function HowItWorks() {
  return (
    <Section id="how-it-works">
      <SectionHeading
        eyebrow="How it works"
        title={howItWorks.heading}
        subtitle={howItWorks.subhead}
      />

      <ol className="mt-14 grid grid-cols-1 gap-6 md:grid-cols-3">
        {howItWorks.steps.map((step, i) => (
          <Reveal as="li" key={step.n} delay={i * 0.12}>
            <div className="relative h-full rounded-2xl border border-line bg-surface/60 p-7">
              <span className="font-display text-5xl font-bold text-accent/25">
                {step.n}
              </span>
              <h3 className="mt-4 font-display text-xl font-semibold text-white">
                {step.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                {step.body}
              </p>
            </div>
          </Reveal>
        ))}
      </ol>
    </Section>
  );
}
