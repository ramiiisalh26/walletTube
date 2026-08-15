import { useState, type FormEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Check, Loader2, Puzzle } from "lucide-react";
import { Section } from "./ui/Section";
import { SectionHeading } from "./ui/SectionHeading";
import { Reveal } from "./ui/Reveal";
import { waitlist } from "../lib/copy";
import { EXTENSION_URL, HAS_WAITLIST_BACKEND, WAITLIST_URL } from "../lib/env";

type Status = "idle" | "loading" | "success" | "error";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function Waitlist() {
  const reduce = useReducedMotion();
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!EMAIL_RE.test(email)) {
      setStatus("error");
      setMessage(waitlist.invalidEmail);
      return;
    }

    setStatus("loading");
    setMessage("");

    // Demo mode: no backend configured → simulate a successful signup.
    if (!HAS_WAITLIST_BACKEND) {
      window.setTimeout(() => setStatus("success"), 700);
      return;
    }

    try {
      const res = await fetch(WAITLIST_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setStatus("success");
    } catch {
      setStatus("error");
      setMessage(waitlist.errorText);
    }
  }

  return (
    <Section id="waitlist">
      <div className="relative overflow-hidden rounded-3xl border border-line bg-surface/60 px-6 py-16 sm:px-12">
        <div className="pointer-events-none absolute left-1/2 top-0 h-48 w-48 -translate-x-1/2 rounded-full bg-accent/20 blur-[90px]" />

        <div className="relative mx-auto max-w-2xl text-center">
          <SectionHeading title={waitlist.heading} subtitle={waitlist.subhead} />

          <Reveal className="mt-10">
            <AnimatePresence mode="wait">
              {status === "success" ? (
                <motion.div
                  key="success"
                  initial={reduce ? false : { opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-2xl border border-accent/40 bg-accent/10 px-6 py-8"
                >
                  <motion.span
                    initial={reduce ? false : { scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", stiffness: 260, damping: 18 }}
                    className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-white"
                  >
                    <Check className="h-6 w-6" />
                  </motion.span>
                  <p className="font-display text-xl font-semibold text-white">
                    {waitlist.successTitle}
                  </p>
                  <p className="text-sm text-zinc-400">{waitlist.successBody}</p>
                </motion.div>
              ) : (
                <motion.div
                  key="form"
                  initial={false}
                  exit={reduce ? undefined : { opacity: 0, y: -8 }}
                >
                  <form
                    onSubmit={handleSubmit}
                    noValidate
                    className="mx-auto flex max-w-md flex-col gap-3 sm:flex-row"
                  >
                    <label htmlFor="email" className="sr-only">
                      Email address
                    </label>
                    <input
                      id="email"
                      type="email"
                      required
                      value={email}
                      onChange={(e) => {
                        setEmail(e.target.value);
                        if (status === "error") setStatus("idle");
                      }}
                      placeholder={waitlist.placeholder}
                      aria-invalid={status === "error"}
                      aria-describedby="waitlist-msg"
                      className="flex-1 rounded-full border border-line bg-ink/60 px-5 py-3 text-sm text-white outline-none transition-colors placeholder:text-zinc-500 focus:border-accent"
                    />
                    <button
                      type="submit"
                      disabled={status === "loading"}
                      className="inline-flex items-center justify-center gap-2 rounded-full bg-accent px-6 py-3 text-sm font-semibold text-white shadow-glow transition-all hover:bg-accent-600 disabled:opacity-60"
                    >
                      {status === "loading" ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <>
                          {waitlist.cta}
                          <ArrowRight className="h-4 w-4" />
                        </>
                      )}
                    </button>
                  </form>

                  <p
                    id="waitlist-msg"
                    aria-live="polite"
                    className="mt-3 min-h-[1.25rem] text-sm text-accent-300"
                  >
                    {status === "error" ? message : ""}
                  </p>

                  <div className="mt-2">
                    <a
                      href={EXTENSION_URL}
                      className="inline-flex items-center gap-2 text-sm text-zinc-400 underline-offset-4 transition-colors hover:text-white hover:underline"
                    >
                      <Puzzle className="h-4 w-4" />
                      {waitlist.extensionCta}
                    </a>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </Reveal>

          {/* Social proof */}
          <Reveal className="mt-12" delay={0.1}>
            <p className="text-xs uppercase tracking-[0.2em] text-zinc-600">
              {waitlist.socialProofLabel}
            </p>
            <div className="mt-3 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm font-medium text-zinc-500">
              {waitlist.socialProof.map((name) => (
                <span key={name}>{name}</span>
              ))}
            </div>
          </Reveal>
        </div>
      </div>
    </Section>
  );
}
