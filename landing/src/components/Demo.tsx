import { Search, Clock, Play, Sparkles, Terminal } from "lucide-react";
import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { demo } from "../lib/copy";

export function Demo() {
  return (
    <Section id="demo">
      <div className="mb-10 flex justify-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-accent/40 bg-accent/10 px-4 py-1.5 text-xs font-semibold text-accent-200">
          <span className="h-1.5 w-1.5 rounded-full bg-accent-300" />
          {demo.badge}
        </span>
      </div>

      <SectionHeading title={demo.heading} subtitle={demo.subhead} />

      <Reveal className="mx-auto mt-12 max-w-2xl">
        <div className="overflow-hidden rounded-2xl border border-line bg-surface/70 shadow-glow">
          {/* Query bar (a pasted error) */}
          <div className="flex items-start gap-3 border-b border-line bg-ink/40 px-5 py-4">
            <Terminal className="mt-0.5 h-5 w-5 shrink-0 text-accent-300" />
            <code className="min-w-0 flex-1 font-mono text-sm text-zinc-300">
              {demo.query}
            </code>
            <Search className="mt-0.5 h-5 w-5 shrink-0 text-zinc-600" />
          </div>

          {/* Results */}
          <div className="flex flex-col gap-3 p-5">
            {demo.results.map((r, i) => (
              <Reveal as="div" key={r.title} delay={i * 0.08}>
                <div className="group flex gap-4 rounded-xl border border-line bg-ink/30 p-4 transition-colors hover:border-accent/40">
                  <div className="relative flex h-16 w-28 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-accent/25 to-accent-700/20">
                    <Play className="h-5 w-5 fill-accent-200 text-accent-200" />
                    <span className="absolute bottom-1 right-1 flex items-center gap-0.5 rounded bg-black/70 px-1 py-0.5 text-[10px] font-medium text-white">
                      <Clock className="h-2.5 w-2.5" />
                      {r.timestamp}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-zinc-100">
                      {r.title}
                    </p>
                    <p className="text-xs text-zinc-500">{r.channel}</p>
                    <p className="mt-1 line-clamp-2 text-xs text-zinc-400">
                      {r.snippet}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="hidden shrink-0 self-center rounded-full bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent-200 transition-colors group-hover:bg-accent group-hover:text-white sm:block"
                  >
                    Jump to moment
                  </button>
                </div>
              </Reveal>
            ))}

            {/* AI answer fallback */}
            <Reveal as="div" delay={0.28}>
              <div className="rounded-xl border border-accent/30 bg-accent/[0.06] p-4">
                <p className="mb-2 inline-flex items-center gap-2 text-xs font-semibold text-accent-200">
                  <Sparkles className="h-3.5 w-3.5" />
                  {demo.aiAnswer.label}
                </p>
                <p className="text-sm leading-relaxed text-zinc-300">
                  {demo.aiAnswer.body}
                </p>
              </div>
            </Reveal>
          </div>
        </div>
      </Reveal>

      <p className="mt-6 text-center text-xs text-zinc-500">
        {demo.disclaimer}
      </p>
    </Section>
  );
}
