"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Clock,
  Eye,
  Sparkles,
  ChevronRight,
  Loader2,
} from "lucide-react";
import Link from "next/link";
import { getVideo } from "@/lib/api";
import type { VideoDetail } from "@/types";

function mockDetail(id: string): VideoDetail {
  return {
    id,
    title: "How to Build a RAG Pipeline with LangChain — Full Tutorial",
    channel: "Fireship",
    thumbnail: `https://picsum.photos/seed/${id}/1280/720`,
    duration: "18:42",
    publishedAt: new Date(Date.now() - 14 * 86400000).toISOString(),
    viewCount: 1_240_000,
    relevanceScore: 0.94,
    matchReason: 'Discusses "RAG pipeline" at 3:14 and 9:45',
    description:
      "In this video we build a full retrieval-augmented generation pipeline using LangChain, Pinecone, and GPT-4. We cover chunking strategies, embedding models, and how to evaluate retrieval quality.",
    aiSummary:
      "This video walks through building a production-ready RAG system. The author demonstrates document ingestion, embedding with OpenAI's ada-002, vector storage in Pinecone, and query-time retrieval with LangChain's RetrievalQA chain. Key insights include chunk overlap strategies and using MMR for diverse retrieval.",
    keyMoments: [
      { startSec: 0,    label: "Introduction & Architecture Overview" },
      { startSec: 188,  label: "Setting up Pinecone & Embedding Documents" },
      { startSec: 540,  label: "Building the RetrievalQA Chain" },
      { startSec: 820,  label: "Evaluating Retrieval Quality" },
      { startSec: 1020, label: "Deployment & Caching Tips" },
    ],
    tags: ["LangChain", "RAG", "AI", "Python", "Vector DB", "LLM"],
  };
}

function secToTimestamp(s: number) {
  const m   = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export default function VideoPage() {
  const { id }    = useParams<{ id: string }>();
  const router    = useRouter();
  const [video, setVideo]     = useState<VideoDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        setVideo(await getVideo(id));
      } catch {
        setVideo(mockDetail(id));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  if (loading)  return (
    <div className="flex justify-center items-center min-h-[60vh]">
      <Loader2 className="w-7 h-7 text-brand-600 animate-spin opacity-60" />
    </div>
  );
  if (!video) return null;

  const embedUrl = `https://www.youtube.com/embed/${id}?rel=0&modestbranding=1`;

  return (
    <div className="relative min-h-[calc(100vh-3.5rem)]">
      {/* Ambient */}
      <div
        className="fixed inset-0 pointer-events-none"
        style={{ background: "radial-gradient(ellipse 60% 35% at 50% -5%, rgba(220,38,38,0.06) 0%, transparent 65%)" }}
      />

      <div className="relative max-w-6xl mx-auto px-4 sm:px-6 py-8 card-in" style={{ animationDelay: "0ms" }}>

        {/* Back */}
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-white/30 hover:text-white/70 text-sm mb-7 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back
        </button>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-7">

          {/* ── Left ─────────────────────────────── */}
          <div className="flex flex-col gap-6">

            {/* Player */}
            <div className="relative aspect-video rounded-2xl overflow-hidden bg-black
              shadow-[0_0_60px_-10px_rgba(0,0,0,0.8)]
              border border-white/[0.06]">
              <iframe
                src={embedUrl}
                title={video.title}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
                className="absolute inset-0 w-full h-full"
              />
            </div>

            {/* Title */}
            <div>
              <h1 className="text-lg sm:text-xl font-semibold text-white leading-snug mb-3">
                {video.title}
              </h1>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-white/35">
                <span className="text-white/70 font-medium">{video.channel}</span>
                {video.viewCount && (
                  <span className="flex items-center gap-1.5">
                    <Eye className="w-3.5 h-3.5" />
                    {video.viewCount.toLocaleString()}
                  </span>
                )}
                {video.duration && (
                  <span className="flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5" />
                    {video.duration}
                  </span>
                )}
              </div>

              {/* AI match */}
              {video.relevanceScore && (
                <div className="mt-3 inline-flex items-center gap-2
                  bg-brand-600/10 border border-brand-600/20
                  text-brand-400 text-xs font-semibold px-3 py-1.5 rounded-full">
                  <Sparkles className="w-3 h-3" />
                  {Math.round(video.relevanceScore * 100)}% match
                  {video.matchReason && (
                    <span className="font-normal text-brand-300/80">— {video.matchReason}</span>
                  )}
                </div>
              )}
            </div>

            {/* Tags */}
            {video.tags?.length && (
              <div className="flex flex-wrap gap-2">
                {video.tags.map((t) => (
                  <Link
                    key={t}
                    href={`/search?q=${encodeURIComponent(t)}`}
                    className="text-xs text-white/35 hover:text-white/70
                      border border-white/[0.08] hover:border-white/[0.18]
                      px-3 py-1 rounded-full transition-all"
                  >
                    {t}
                  </Link>
                ))}
              </div>
            )}

            {/* Description */}
            {video.description && (
              <div className="bg-white/[0.025] border border-white/[0.06] rounded-xl p-4">
                <p className="text-sm text-white/35 leading-relaxed whitespace-pre-line line-clamp-5">
                  {video.description}
                </p>
              </div>
            )}
          </div>

          {/* ── Right ────────────────────────────── */}
          <div className="flex flex-col gap-4">

            {/* AI Summary */}
            {video.aiSummary && (
              <div className="bg-white/[0.025] border border-brand-600/20
                backdrop-blur-xl rounded-2xl p-5">
                <div className="flex items-center gap-2 mb-3">
                  <Sparkles className="w-3.5 h-3.5 text-brand-400" />
                  <h2 className="text-xs font-semibold text-white/70 uppercase tracking-wider">
                    AI Summary
                  </h2>
                </div>
                <p className="text-sm text-white/50 leading-relaxed">{video.aiSummary}</p>
              </div>
            )}

            {/* Key Moments */}
            {video.keyMoments?.length && (
              <div className="bg-white/[0.025] border border-white/[0.06]
                backdrop-blur-xl rounded-2xl p-5">
                <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-4">
                  Key Moments
                </h2>
                <ul className="flex flex-col gap-1">
                  {video.keyMoments.map((m) => (
                    <li key={m.startSec}>
                      <a
                        href={`https://www.youtube.com/watch?v=${id}&t=${m.startSec}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-3 group py-1.5 rounded-lg
                          hover:bg-white/[0.04] px-1.5 transition-colors"
                      >
                        <span className="font-mono text-[11px] text-brand-500/80 shrink-0 w-9">
                          {secToTimestamp(m.startSec)}
                        </span>
                        <span className="text-xs text-white/45 group-hover:text-white/75
                          transition-colors flex-1 line-clamp-1">
                          {m.label}
                        </span>
                        <ChevronRight className="w-3 h-3 text-white/15 group-hover:text-brand-500/60 shrink-0 transition-colors" />
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* YouTube CTA */}
            <a
              href={`https://www.youtube.com/watch?v=${id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2.5
                bg-[#ff0000]/90 hover:bg-[#ff0000]
                text-white text-sm font-semibold px-4 py-3 rounded-xl transition-colors"
            >
              <svg className="w-4 h-4 fill-white" viewBox="0 0 24 24">
                <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.4.6A3 3 0 0 0 .5 6.2C0 8.1 0 12 0 12s0 3.9.5 5.8a3 3 0 0 0 2.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 0 0 2.1-2.1C24 15.9 24 12 24 12s0-3.9-.5-5.8zM9.7 15.5V8.5l6.3 3.5-6.3 3.5z"/>
              </svg>
              Watch on YouTube
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
