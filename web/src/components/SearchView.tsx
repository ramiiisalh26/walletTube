"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2, Lock, SearchX, ServerCrash } from "lucide-react";
import { ApiError, searchVideos } from "@/lib/api";
import { getSessionId } from "@/lib/session";
import type { SearchResponse } from "@/types/api";
import { SearchBar } from "./SearchBar";
import { ResultCard } from "./ResultCard";

const PAGE_SIZE = 20;

export function SearchView() {
  const params = useSearchParams();
  const query = params.get("q") ?? "";

  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limitReached, setLimitReached] = useState<string | null>(null);

  useEffect(() => {
    if (!query) {
      setData(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setLimitReached(null);

    searchVideos({ query, session_id: getSessionId(), size: PAGE_SIZE })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 429) {
          setLimitReached(err.message);
        } else {
          setError(
            err instanceof ApiError
              ? err.message
              : "Could not reach the API. Make sure the backend is running on :8080.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [query]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-6">
        <SearchBar initialQuery={query} />
      </div>

      {!query && (
        <p className="py-16 text-center text-zinc-500">
          Type a question above to search across indexed videos.
        </p>
      )}

      {loading && (
        <div className="flex flex-col items-center gap-3 py-20 text-zinc-500">
          <Loader2 className="h-6 w-6 animate-spin" />
          <span>Searching transcripts…</span>
        </div>
      )}

      {limitReached && !loading && (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-brand/40 bg-brand/5 px-6 py-12 text-center">
          <Lock className="h-8 w-8 text-brand" />
          <div>
            <h2 className="text-lg font-semibold">Daily free limit reached</h2>
            <p className="mt-1 max-w-sm text-sm text-zinc-400">{limitReached}</p>
          </div>
          <Link
            href="/pricing"
            className="rounded-full bg-brand px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-brand-hover"
          >
            Upgrade to Pro — unlimited searches
          </Link>
          <p className="text-xs text-zinc-500">
            Your free searches reset at midnight UTC.
          </p>
        </div>
      )}

      {error && !loading && (
        <div className="flex flex-col items-center gap-3 py-20 text-center text-zinc-400">
          <ServerCrash className="h-8 w-8 text-brand" />
          <p>{error}</p>
        </div>
      )}

      {data && !loading && !error && !limitReached && (
        <>
          <div className="mb-4 flex items-center justify-between text-sm text-zinc-500">
            <span>
              {data.total_results} result{data.total_results === 1 ? "" : "s"}
              {data.detected_industry && (
                <span className="ml-2 rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">
                  {data.detected_industry}
                </span>
              )}
            </span>
            <span>{data.latency_ms} ms</span>
          </div>

          {data.free_searches_limit !== null && (
            <FreeUsageBar
              remaining={data.free_searches_remaining ?? 0}
              limit={data.free_searches_limit}
            />
          )}

          {data.indexing_more && (
            <p className="mb-4 rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-sm text-zinc-400">
              Indexing more videos for this query — check back soon for fresher
              results.
            </p>
          )}

          {data.results.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-20 text-zinc-500">
              <SearchX className="h-8 w-8" />
              <p>No matches yet. Try rephrasing your question.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {data.results.map((result, i) => (
                <ResultCard
                  key={`${result.video_id}-${result.start_time}-${i}`}
                  result={result}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function FreeUsageBar({
  remaining,
  limit,
}: {
  remaining: number;
  limit: number;
}) {
  const isLast = remaining <= 1;
  return (
    <div className="mb-4 flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-sm">
      <span className={isLast ? "text-brand" : "text-zinc-400"}>
        {remaining} of {limit} free searches left today
      </span>
      <Link href="/pricing" className="text-brand hover:text-brand-hover">
        Go unlimited →
      </Link>
    </div>
  );
}
