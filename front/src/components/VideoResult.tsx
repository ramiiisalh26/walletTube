"use client";

import Image from "next/image";
import { Eye, Sparkles, X, ChevronRight, ExternalLink } from "lucide-react";
import { useState } from "react";
import type { Video } from "@/types";

/* ── helpers ──────────────────────────────────────────────── */
function formatViews(n?: number) {
  if (!n) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function timeAgo(iso?: string) {
  if (!iso) return "";
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d < 1) return "today";
  if (d < 7) return `${d}d ago`;
  if (d < 30) return `${Math.floor(d / 7)}w ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

function secToTimestamp(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* ── Word-by-word title ───────────────────────────────────── */
function GlassTitle({ text, baseDelay }: { text: string; baseDelay: number }) {
  const words = text.split(" ");
  return (
    <span aria-label={text}>
      {words.map((word, i) => (
        <span
          key={i}
          className="word-in"
          style={{ animationDelay: `${baseDelay + 60 + i * 35}ms` }}
        >
          {word}
          {i < words.length - 1 ? " " : ""}
        </span>
      ))}
    </span>
  );
}

/* ── Props ────────────────────────────────────────────────── */
interface Props {
  video: Video;
  index: number;
  isExpanded: boolean;
  onExpand: (id: string | null) => void;
}

/* ── Expanded panel (replaces thumbnail area, full width) ─── */
function ExpandedPanel({
  video,
  onClose,
}: {
  video: Video;
  onClose: () => void;
}) {
  const isMock = video.id.startsWith("mock-");

  return (
    <div className="flex flex-col gap-5 card-in" style={{ animationDelay: "0ms" }}>
      {/* Player */}
      <div className="relative w-full aspect-video rounded-xl overflow-hidden bg-black border border-white/[0.06]">
        {isMock ? (
          /* Placeholder for mock data */
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-white/[0.03]">
            <Image
              src={video.thumbnail}
              alt={video.title}
              fill
              className="object-cover opacity-30"
              unoptimized
            />
            <div className="relative z-10 flex flex-col items-center gap-2 text-center px-6">
              <span className="text-white/50 text-sm">Preview unavailable for mock data</span>
              <a
                href={`https://www.youtube.com/watch?v=${video.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs text-brand-400 hover:text-brand-300 transition-colors"
              >
                Open on YouTube <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>
        ) : (
          <iframe
            src={`https://www.youtube.com/embed/${video.id}?autoplay=1&rel=0&modestbranding=1`}
            title={video.title}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            className="absolute inset-0 w-full h-full"
          />
        )}
      </div>

      {/* Meta row */}
      <div className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1.5">
            <p className="text-sm font-semibold text-white leading-snug">{video.title}</p>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-white/35">
              <span className="text-white/55 font-medium">{video.channel}</span>
              {video.viewCount && (
                <span className="flex items-center gap-1">
                  <Eye className="w-3 h-3" />{formatViews(video.viewCount)}
                </span>
              )}
              {video.publishedAt && <span>{timeAgo(video.publishedAt)}</span>}
              {video.duration && <span>{video.duration}</span>}
            </div>
          </div>

          {/* Close */}
          <button
            onClick={(e) => { e.stopPropagation(); onClose(); }}
            className="shrink-0 p-1.5 rounded-lg hover:bg-white/[0.06] text-white/30 hover:text-white/70 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* AI match */}
        {video.relevanceScore && (
          <div className="inline-flex self-start items-center gap-1.5 bg-brand-600/10 border border-brand-600/20 text-brand-400 text-[10px] font-bold uppercase tracking-wide px-2.5 py-1 rounded-full">
            <Sparkles className="w-3 h-3" />
            {Math.round(video.relevanceScore * 100)}% match
            {video.matchReason && (
              <span className="font-normal text-brand-300/80 normal-case tracking-normal">
                — {video.matchReason}
              </span>
            )}
          </div>
        )}
      </div>

      {/* AI summary (mock) */}
      <div className="bg-white/[0.025] border border-brand-600/15 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-2">
          <Sparkles className="w-3.5 h-3.5 text-brand-400" />
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/40">AI Summary</span>
        </div>
        <p className="text-xs text-white/45 leading-relaxed">
          {video.description ??
            `This video covers ${video.title.toLowerCase()}. The author walks through key concepts, practical examples, and actionable takeaways that apply to real-world use cases.`}
        </p>
      </div>

      {/* Bottom actions */}
      <div className="flex items-center justify-between">
        <a
          href={`https://www.youtube.com/watch?v=${video.id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 text-xs text-white/35 hover:text-white/60 transition-colors"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          Open on YouTube
        </a>
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className="flex items-center gap-1.5 text-xs text-white/25 hover:text-white/50 transition-colors"
        >
          <ChevronRight className="w-3.5 h-3.5 rotate-90" />
          Collapse
        </button>
      </div>
    </div>
  );
}

/* ── Main component ───────────────────────────────────────── */
export default function VideoResult({ video, index, isExpanded, onExpand }: Props) {
  const [hovered, setHovered] = useState(false);

  const cardDelay    = index * 70;
  const wordBaseDelay = cardDelay + 200;
  const score        = video.relevanceScore ?? null;
  const views        = formatViews(video.viewCount);
  const when         = timeAgo(video.publishedAt);

  function handleClick(e: React.MouseEvent) {
    e.preventDefault();
    onExpand(isExpanded ? null : video.id);
  }

  return (
    <div
      onClick={handleClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`
        card-in relative rounded-2xl
        border
        backdrop-blur-xl
        transition-all duration-400 ease-out
        cursor-pointer outline-none select-none
        ${isExpanded
          ? "border-brand-600/30 bg-white/[0.04] shadow-[0_0_60px_-10px_rgba(220,38,38,0.12)] p-5 sm:p-6"
          : "border-white/[0.06] hover:border-brand-600/35 hover:bg-white/[0.05] hover:shadow-[0_0_40px_0_rgba(220,38,38,0.06)] p-4 sm:p-5"
        }
      `}
      style={{ animationDelay: `${cardDelay}ms` }}
    >
      {/* ── COLLAPSED view ────────────────────────────────────── */}
      {!isExpanded && (
        <div className="flex gap-4 sm:gap-5">
          {/* Thumbnail */}
          <div
            className="relative flex-shrink-0 w-[140px] sm:w-[200px] md:w-[240px] aspect-video rounded-xl overflow-hidden bg-white/[0.03] transition-transform duration-500 ease-out"
            style={{ transform: hovered ? "scale(1.03)" : "scale(1)" }}
          >
            <Image
              src={video.thumbnail}
              alt={video.title}
              fill
              sizes="240px"
              className="object-cover"
              unoptimized
            />
            {/* Scan line */}
            <div className="scan-line" />
            {/* Play overlay */}
            <div
              className="absolute inset-0 flex items-center justify-center bg-black/30 transition-opacity duration-300"
              style={{ opacity: hovered ? 1 : 0 }}
            >
              <svg viewBox="0 0 24 24" className="w-10 h-10 text-white drop-shadow-lg fill-white">
                <circle cx="12" cy="12" r="11" fill="rgba(0,0,0,0.5)" />
                <path d="M10 8l6 4-6 4V8z" fill="white" />
              </svg>
            </div>
            {/* Duration */}
            <span className="absolute bottom-1.5 right-1.5 bg-black/75 backdrop-blur-sm text-white text-[10px] font-semibold px-1.5 py-0.5 rounded">
              {video.duration}
            </span>
          </div>

          {/* Content */}
          <div className="flex flex-col justify-between min-w-0 flex-1 py-0.5">
            <div className="flex flex-col gap-2">
              {score !== null && (
                <div
                  className="word-in self-start flex items-center gap-1.5 text-[10px] font-bold tracking-wide uppercase text-brand-400"
                  style={{ animationDelay: `${wordBaseDelay}ms` }}
                >
                  <Sparkles className="w-3 h-3" />
                  {Math.round(score * 100)}% match
                </div>
              )}
              <h3 className="text-sm sm:text-base font-semibold text-white/90 leading-snug group-hover:text-white transition-colors line-clamp-3">
                <GlassTitle text={video.title} baseDelay={wordBaseDelay} />
              </h3>
            </div>

            <div className="flex flex-col gap-1.5 mt-3">
              <div
                className="word-in flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-white/40"
                style={{ animationDelay: `${wordBaseDelay + 120}ms` }}
              >
                <span className="text-white/60 font-medium">{video.channel}</span>
                {views && <span className="flex items-center gap-1"><Eye className="w-3 h-3" />{views}</span>}
                {when  && <span>{when}</span>}
              </div>
              {video.matchReason && (
                <span
                  className="word-in text-xs text-brand-400"
                  style={{ animationDelay: `${wordBaseDelay + 200}ms` }}
                >
                  ✦ {video.matchReason}
                </span>
              )}
            </div>
          </div>

          {/* Left glow edge */}
          <div
            className="absolute left-0 top-4 bottom-4 w-[2px] rounded-full bg-gradient-to-b from-transparent via-brand-600 to-transparent transition-opacity duration-300"
            style={{ opacity: hovered ? 1 : 0 }}
          />
        </div>
      )}

      {/* ── EXPANDED view ─────────────────────────────────────── */}
      {isExpanded && (
        <ExpandedPanel video={video} onClose={() => onExpand(null)} />
      )}
    </div>
  );
}
