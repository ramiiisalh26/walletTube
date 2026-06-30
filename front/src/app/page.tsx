"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Search, Sparkles, Zap, Brain } from "lucide-react";

const EXAMPLES = [
  "How to build a RAG pipeline with LangChain",
  "Beginner guitar fingerpicking patterns",
  "Transformer attention mechanism visualized",
  "React Server Components deep dive",
  "Best pasta carbonara technique step by step",
];

export default function HomePage() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  function go(q: string) {
    if (!q.trim()) return;
    router.push(`/search?q=${encodeURIComponent(q.trim())}`);
  }

  return (
    <div className="relative min-h-[calc(100vh-4rem)] flex flex-col items-center justify-center px-4 py-20 overflow-hidden">

      {/* Background ambient glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: [
            "radial-gradient(ellipse 70% 50% at 50% -5%, rgba(220,38,38,0.10) 0%, transparent 65%)",
            "radial-gradient(ellipse 40% 30% at 80% 80%, rgba(220,38,38,0.04) 0%, transparent 60%)",
          ].join(", "),
        }}
      />

      {/* Hero */}
      <div className="relative text-center mb-12">
        <div className="inline-flex items-center gap-2 bg-brand-600/10 border border-brand-600/25
          text-brand-400 text-xs font-semibold px-3 py-1.5 rounded-full mb-8 tracking-wide">
          <Sparkles className="w-3 h-3" />
          AI-Powered Semantic Search
        </div>

        <h1 className="text-6xl sm:text-7xl font-bold tracking-tight mb-5">
          <span className="text-brand-500">Bucket</span>
          <span className="text-white">Tube</span>
        </h1>

        <p className="text-white/40 text-base sm:text-lg max-w-sm mx-auto leading-relaxed">
          Find exactly what you're looking for across millions of YouTube
          videos — by meaning, not keywords.
        </p>
      </div>

      {/* Search */}
      <form
        onSubmit={(e) => { e.preventDefault(); go(query); }}
        className="relative w-full max-w-2xl"
      >
        {/* Glass search bar */}
        <div className="relative flex items-center
          bg-white/[0.04] border border-white/[0.10]
          hover:border-white/[0.18] focus-within:border-brand-600/60
          focus-within:shadow-[0_0_0_3px_rgba(220,38,38,0.12)]
          rounded-2xl transition-all duration-300 backdrop-blur-xl">
          <Search className="absolute left-5 w-5 h-5 text-white/25 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask anything about a video topic…"
            autoFocus
            className="w-full bg-transparent text-white placeholder:text-white/25
              text-base pl-14 pr-36 py-4 outline-none rounded-2xl"
          />
          <button
            type="submit"
            disabled={!query.trim()}
            className="absolute right-2 bg-brand-600 hover:bg-brand-500
              disabled:opacity-30 disabled:cursor-not-allowed
              text-white text-sm font-semibold px-5 py-2.5 rounded-xl
              transition-all"
          >
            Search
          </button>
        </div>
      </form>

      {/* Example chips */}
      <div className="flex flex-wrap justify-center gap-2 mt-5 max-w-2xl">
        {EXAMPLES.map((q) => (
          <button
            key={q}
            onClick={() => go(q)}
            className="text-xs text-white/30 hover:text-white/70
              border border-white/[0.07] hover:border-white/[0.18]
              px-3 py-1.5 rounded-full transition-all"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Features */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-2xl w-full mt-16">
        {[
          { icon: <Brain className="w-4 h-4" />,     title: "Semantic Understanding", desc: "Understands intent, not just words" },
          { icon: <Zap className="w-4 h-4" />,        title: "Instant Results",         desc: "Sub-second AI-ranked matches" },
          { icon: <Sparkles className="w-4 h-4" />,   title: "Match Explanations",      desc: "Know why each video was picked" },
        ].map(({ icon, title, desc }) => (
          <div
            key={title}
            className="flex flex-col items-center text-center gap-2
              bg-white/[0.025] border border-white/[0.06]
              backdrop-blur-xl rounded-xl p-4"
          >
            <span className="text-brand-500">{icon}</span>
            <p className="text-xs font-semibold text-white/80">{title}</p>
            <p className="text-xs text-white/30">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
