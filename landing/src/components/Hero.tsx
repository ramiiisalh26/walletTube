import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Search, Clock, ArrowRight, Play } from "lucide-react";
import { Container } from "./ui/Container";
import { Button } from "./ui/Button";
import { Wordmark } from "./ui/Wordmark";
import { ConvergingBackground } from "./ui/ConvergingBackground";
import { useRotatingIndex } from "../hooks/useRotatingIndex";
import { hero } from "../lib/copy";

export function Hero() {
  const reduce = useReducedMotion();
  const index = useRotatingIndex(hero.queries.length, 3200, !reduce);
  const [phase, setPhase] = useState<"searching" | "found">("found");
  const current = hero.queries[index];

  // Two-phase per query: brief "searching" shimmer, then the result resolves.
  useEffect(() => {
    if (reduce) {
      setPhase("found");
      return;
    }
    setPhase("searching");
    const t = window.setTimeout(() => setPhase("found"), 750);
    return () => window.clearTimeout(t);
  }, [index, reduce]);

  return (
    <header id="top" className="relative overflow-hidden">
      <ConvergingBackground />

      <Container className="relative">
        {/* Nav */}
        <nav className="flex items-center justify-between py-6">
          <Wordmark />
          <Button href="#waitlist" className="px-5 py-2 text-sm">
            {hero.primaryCta}
          </Button>
        </nav>

        {/* Hero content */}
        <div className="mx-auto max-w-3xl pb-24 pt-16 text-center sm:pt-24">
          <motion.p
            initial={reduce ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="mb-5 inline-block rounded-full border border-line bg-surface/60 px-4 py-1.5 text-xs font-medium uppercase tracking-[0.18em] text-accent-300"
          >
            {hero.eyebrow}
          </motion.p>

          <motion.h1
            initial={reduce ? false : { opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05 }}
            className="font-display text-4xl font-bold leading-[1.1] text-white text-balance sm:text-6xl"
          >
            {hero.headline}
          </motion.h1>

          <motion.p
            initial={reduce ? false : { opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.12 }}
            className="mx-auto mt-6 max-w-xl text-lg text-zinc-400 text-balance"
          >
            {hero.subhead}
          </motion.p>

          {/* Fake search bar */}
          <motion.div
            initial={reduce ? false : { opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="mx-auto mt-10 max-w-xl"
          >
            <div className="flex items-center gap-3 rounded-2xl border border-line bg-surface/80 px-5 py-4 text-left shadow-glow backdrop-blur">
              <Search className="h-5 w-5 shrink-0 text-accent-300" />
              <div className="min-w-0 flex-1">
                <span className="block text-xs font-medium uppercase tracking-wider text-zinc-500">
                  {current.field}
                </span>
                <AnimatePresence mode="wait">
                  <motion.span
                    key={current.text}
                    initial={reduce ? false : { opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={reduce ? undefined : { opacity: 0, y: -6 }}
                    transition={{ duration: 0.3 }}
                    className="block truncate text-base text-zinc-100"
                  >
                    {current.text}
                  </motion.span>
                </AnimatePresence>
              </div>
            </div>

            {/* Resolving result card */}
            <div className="mt-3 min-h-[64px]">
              <AnimatePresence mode="wait">
                {phase === "searching" ? (
                  <motion.div
                    key="searching"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="h-16 w-full overflow-hidden rounded-xl border border-line bg-surface/50"
                  >
                    <div className="h-full w-full animate-shimmer bg-shimmer" />
                  </motion.div>
                ) : (
                  <motion.div
                    key={`found-${current.text}`}
                    initial={reduce ? false : { opacity: 0, y: 8, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={reduce ? undefined : { opacity: 0, y: -8 }}
                    transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                    className="flex items-center gap-3 rounded-xl border border-accent/30 bg-surface px-4 py-3 text-left"
                  >
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent-300">
                      <Play className="h-4 w-4 fill-current" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-zinc-200">
                        {current.source}
                      </p>
                      <p className="flex items-center gap-1 text-xs text-accent-300">
                        <Clock className="h-3 w-3" />
                        found at {current.timestamp}
                      </p>
                    </div>
                    <ArrowRight className="h-4 w-4 shrink-0 text-zinc-500" />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>

          {/* CTAs */}
          <motion.div
            initial={reduce ? false : { opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.3 }}
            className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row"
          >
            <Button href="#waitlist">
              {hero.primaryCta}
              <ArrowRight className="h-4 w-4" />
            </Button>
            <Button href="#demo" variant="secondary">
              {hero.secondaryCta}
            </Button>
          </motion.div>
        </div>
      </Container>
    </header>
  );
}
