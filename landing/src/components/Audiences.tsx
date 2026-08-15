import type { LucideIcon } from "lucide-react";
import {
  Code2,
  GraduationCap,
  Music,
  ChefHat,
  Dumbbell,
  Stethoscope,
  Wrench,
  Languages,
  PenTool,
} from "lucide-react";
import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { audiences } from "../lib/copy";

const ICONS: Record<string, LucideIcon> = {
  Code2,
  GraduationCap,
  Music,
  ChefHat,
  Dumbbell,
  Stethoscope,
  Wrench,
  Languages,
  PenTool,
};

export function Audiences() {
  return (
    <Section id="audiences">
      <SectionHeading
        eyebrow="Who it's for"
        title={audiences.heading}
        subtitle={audiences.subhead}
      />

      <ul className="mt-14 grid grid-cols-2 gap-4 sm:grid-cols-3">
        {audiences.fields.map((field, i) => {
          const Icon = ICONS[field.icon] ?? Code2;
          return (
            <Reveal as="li" key={field.label} delay={(i % 3) * 0.06}>
              <div className="group relative h-full overflow-hidden rounded-2xl border border-line bg-surface/60 p-5 transition-all duration-200 hover:-translate-y-1 hover:border-accent/50">
                <div className="flex items-center justify-between">
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent-300 transition-colors group-hover:bg-accent group-hover:text-white">
                    <Icon className="h-5 w-5" />
                  </span>
                  {field.live && (
                    <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent-200">
                      Live
                    </span>
                  )}
                </div>
                <p className="mt-4 font-semibold text-white">{field.label}</p>
                <p className="mt-1 text-xs text-zinc-500">{field.example}</p>
              </div>
            </Reveal>
          );
        })}
      </ul>
    </Section>
  );
}
