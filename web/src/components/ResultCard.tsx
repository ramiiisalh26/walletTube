"use client";

import { useState } from "react";
import { Clock, Eye, Play } from "lucide-react";
import type { SearchResult } from "@/types/api";
import { formatTimestamp, formatViews, relevancePercent } from "@/lib/format";

export function ResultCard({ result }: { result: SearchResult }) {
  const [playing, setPlaying] = useState(false);
  const relevance = relevancePercent(result.similarity);

  return (
    <article className="flex flex-col gap-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-zinc-700 sm:flex-row">
      {/* Thumbnail / inline player */}
      <div className="relative aspect-video w-full shrink-0 overflow-hidden rounded-lg bg-zinc-800 sm:w-64">
        {playing ? (
          <iframe
            src={result.embed_url}
            title={result.title}
            allow="accelerated-encoder; autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
            className="h-full w-full"
          />
        ) : (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            className="group relative block h-full w-full"
            aria-label={`Play ${result.title} at ${formatTimestamp(result.start_time)}`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={
                result.thumbnail_url ??
                `https://i.ytimg.com/vi/${result.video_id}/hqdefault.jpg`
              }
              alt={result.title}
              className="h-full w-full object-cover"
            />
            <span className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 transition-opacity group-hover:opacity-100">
              <Play className="h-10 w-10 fill-white text-white" />
            </span>
            <span className="absolute bottom-1.5 right-1.5 flex items-center gap-1 rounded bg-black/80 px-1.5 py-0.5 text-xs font-medium">
              <Clock className="h-3 w-3" />
              {formatTimestamp(result.start_time)}
            </span>
          </button>
        )}
      </div>

      {/* Details */}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <a
            href={result.youtube_url}
            target="_blank"
            rel="noopener noreferrer"
            className="line-clamp-2 font-medium leading-snug hover:text-brand"
          >
            {result.title}
          </a>
          <span
            className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400"
            title="Relevance score"
          >
            {relevance}% match
          </span>
        </div>

        <div className="flex items-center gap-3 text-xs text-zinc-500">
          {result.channel_name && <span>{result.channel_name}</span>}
          <span className="flex items-center gap-1">
            <Eye className="h-3 w-3" />
            {formatViews(result.view_count)}
          </span>
        </div>

        <p className="line-clamp-3 text-sm text-zinc-300">
          &ldquo;{result.text}&rdquo;
        </p>

        <button
          type="button"
          onClick={() => setPlaying(true)}
          className="mt-auto inline-flex w-fit items-center gap-1.5 text-sm text-brand hover:text-brand-hover"
        >
          <Play className="h-3.5 w-3.5 fill-current" />
          Jump to {formatTimestamp(result.start_time)}
        </button>
      </div>
    </article>
  );
}
