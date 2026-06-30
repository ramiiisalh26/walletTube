"use client";

import { useEffect, useState, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SlidersHorizontal } from "lucide-react";
import VideoResult from "@/components/VideoResult";
import { searchVideos } from "@/lib/api";
import type { Video } from "@/types";

/* ── Mock fallback while backend is offline ───────────────── */
function mockResults(query: string): Video[] {
  const channels = ["Fireship", "Theo — t3.gg", "Andrej Karpathy", "TechLead", "Traversy Media"];
  const reasons  = [
    `Mentions "${query}" at 3:14`,
    `Deep-dives "${query}" from 8:00 onward`,
    `Compares approaches to "${query}" at 12:45`,
    `Quick overview of "${query}"`,
    undefined,
  ];
  return Array.from({ length: 10 }, (_, i) => ({
    id:             `mock-${i}`,
    title:          `${channels[i % channels.length]} — ${query}: ${["Complete Guide", "Deep Dive", "From Scratch", "Explained", "Full Tutorial", "Best Practices", "In 100 Seconds", "Advanced Tips", "for Beginners", "Pro Edition"][i]}`,
    channel:        channels[i % channels.length],
    thumbnail:      `https://picsum.photos/seed/${encodeURIComponent(query)}-${i}/640/360`,
    duration:       `${Math.floor(Math.random() * 38 + 2)}:${String(Math.floor(Math.random() * 60)).padStart(2, "0")}`,
    publishedAt:    new Date(Date.now() - i * 6 * 86400000).toISOString(),
    viewCount:      Math.floor(Math.random() * 1_800_000 + 8_000),
    relevanceScore: Math.max(0.48, 1 - i * 0.053),
    matchReason:    reasons[i % reasons.length],
  }));
}

/* ── Thinking indicator ────────────────────────────────────── */
function Thinking({ query }: { query: string }) {
  return (
    <div className="flex flex-col items-start gap-3 py-10 px-1 animate-[fadeIn_0.3s_ease_forwards]">
      <div className="flex items-center gap-3 text-sm text-white/40">
        <div className="w-5 h-5 rounded-full border border-brand-600/50 flex items-center justify-center">
          <div className="w-2 h-2 rounded-full bg-brand-600 animate-ping" />
        </div>
        Searching for
        <span className="text-white/70 font-medium">"{query}"</span>
      </div>

      {/* Skeleton results */}
      <div className="w-full flex flex-col gap-4 mt-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="flex gap-4 rounded-2xl p-4 border border-white/[0.04] bg-white/[0.02]"
            style={{ opacity: 1 - i * 0.2 }}
          >
            <div className="flex-shrink-0 w-[200px] aspect-video rounded-xl shimmer" />
            <div className="flex-1 flex flex-col gap-2.5 py-1">
              <div className="shimmer h-3 w-16 rounded" />
              <div className="shimmer h-4 w-4/5 rounded" />
              <div className="shimmer h-4 w-3/5 rounded" />
              <div className="mt-auto shimmer h-3 w-1/3 rounded" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Header: AI answer line ───────────────────────────────── */
function ResultsHeader({
  query,
  total,
  ms,
}: {
  query: string;
  total: number;
  ms: number;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-2 mb-8">
      <div>
        <p className="text-xs text-white/30 uppercase tracking-widest mb-1">
          AI Search
        </p>
        <h2 className="text-xl sm:text-2xl font-semibold text-white leading-tight">
          Results for{" "}
          <span className="text-brand-400">"{query}"</span>
        </h2>
        <p className="text-xs text-white/25 mt-1.5">
          {total.toLocaleString()} videos &middot; {ms}ms
        </p>
      </div>
    </div>
  );
}

type SortKey = "relevance" | "views" | "date";

/* ── Main inner (needs useSearchParams so wrapped in Suspense) */
function SearchPageInner() {
  const searchParams  = useSearchParams();
  const router        = useRouter();
  const query         = searchParams.get("q") ?? "";

  const [videos,     setVideos]     = useState<Video[]>([]);
  const [loading,    setLoading]    = useState(false);
  const [total,      setTotal]      = useState(0);
  const [ms,         setMs]         = useState(0);
  const [sort,       setSort]       = useState<SortKey>("relevance");
  const [page,       setPage]       = useState(1);
  const [hasMore,    setHasMore]    = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const PAGE_SIZE = 20;

  const fetchResults = useCallback(
    async (q: string, p: number, append = false) => {
      if (!q) return;
      setLoading(true);
      try {
        const data = await searchVideos(q, p, PAGE_SIZE);
        setVideos((prev) => append ? [...prev, ...data.results] : data.results);
        setTotal(data.total);
        setMs(data.processingTimeMs);
        setHasMore(p * PAGE_SIZE < data.total);
      } catch {
        const mock = mockResults(q);
        setVideos((prev) => append ? [...prev, ...mock] : mock);
        setTotal(mock.length);
        setMs(38);
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    setPage(1);
    setVideos([]);
    setExpandedId(null);
    fetchResults(query, 1);
  }, [query, fetchResults]);

  function loadMore() {
    const next = page + 1;
    setPage(next);
    fetchResults(query, next, true);
  }

  function sorted(vids: Video[]): Video[] {
    const copy = [...vids];
    if (sort === "relevance") return copy.sort((a, b) => (b.relevanceScore ?? 0) - (a.relevanceScore ?? 0));
    if (sort === "views")     return copy.sort((a, b) => (b.viewCount ?? 0)      - (a.viewCount ?? 0));
    if (sort === "date")      return copy.sort((a, b) => new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime());
    return copy;
  }

  if (!query) { router.replace("/"); return null; }

  const displayVideos = sorted(videos);

  return (
    /*
      Outer: narrow centered column — gives the "answer feed" feel
      not a grid. Max ~780px wide, left-aligned text.
    */
    <div className="min-h-[calc(100vh-4rem)]">
      {/* Ambient background glow */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 40% at 50% -10%, rgba(220,38,38,0.06) 0%, transparent 70%)",
        }}
      />

      <div className="relative max-w-3xl mx-auto px-4 sm:px-6 py-10">
        {/* Sort bar — always visible */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-1.5 text-xs text-white/30">
            <SlidersHorizontal className="w-3.5 h-3.5" />
            Sort
          </div>
          <div className="flex gap-1.5">
            {(["relevance", "views", "date"] as SortKey[]).map((s) => (
              <button
                key={s}
                onClick={() => setSort(s)}
                className={`text-xs px-3 py-1.5 rounded-full border transition-all capitalize ${
                  sort === s
                    ? "bg-brand-600/20 border-brand-600/50 text-brand-400"
                    : "border-white/[0.08] text-white/30 hover:text-white/60 hover:border-white/20"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Loading state */}
        {loading && page === 1 && <Thinking query={query} />}

        {/* Results */}
        {!loading || page > 1 ? (
          <>
            {displayVideos.length > 0 && (
              <ResultsHeader query={query} total={total} ms={ms} />
            )}

            {/* The feed */}
            <div className="flex flex-col gap-3">
              {displayVideos.map((v, i) => (
                <VideoResult
                  key={v.id}
                  video={v}
                  index={i}
                  isExpanded={expandedId === v.id}
                  onExpand={setExpandedId}
                />
              ))}
            </div>

            {/* Empty */}
            {displayVideos.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center py-28 text-center">
                <p className="text-5xl mb-4 opacity-50">🪣</p>
                <p className="text-white/50 text-base font-medium">
                  Nothing found for "{query}"
                </p>
                <p className="text-white/25 text-sm mt-1">
                  Try a different phrasing or broader topic
                </p>
              </div>
            )}

            {/* Load more */}
            {hasMore && (
              <div className="flex justify-center mt-8">
                <button
                  onClick={loadMore}
                  disabled={loading}
                  className="text-sm text-white/40 hover:text-white/70 border border-white/[0.08]
                    hover:border-white/20 px-8 py-2.5 rounded-full transition-all"
                >
                  {loading ? "Loading…" : "Load more"}
                </button>
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense>
      <SearchPageInner />
    </Suspense>
  );
}
