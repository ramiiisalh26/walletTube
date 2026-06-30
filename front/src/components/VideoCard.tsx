import Link from "next/link";
import Image from "next/image";
import { Clock, Eye, Sparkles } from "lucide-react";
import type { Video } from "@/types";

function formatViews(n?: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M views`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K views`;
  return `${n} views`;
}

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const d = Math.floor(diff / 86400000);
  if (d < 1) return "today";
  if (d < 7) return `${d}d ago`;
  if (d < 30) return `${Math.floor(d / 7)}w ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

export default function VideoCard({ video }: { video: Video }) {
  const score = video.relevanceScore ?? null;

  return (
    <Link
      href={`/video/${video.id}`}
      className="group flex flex-col gap-3 animate-fade-in"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video rounded-xl overflow-hidden bg-surface-elevated">
        <Image
          src={video.thumbnail}
          alt={video.title}
          fill
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          className="object-cover group-hover:scale-105 transition-transform duration-300"
          unoptimized
        />

        {/* Duration badge */}
        <span className="absolute bottom-2 right-2 bg-black/80 text-white text-xs font-medium px-1.5 py-0.5 rounded">
          {video.duration}
        </span>

        {/* AI relevance badge */}
        {score !== null && (
          <span className="absolute top-2 left-2 flex items-center gap-1 bg-brand-600/90 backdrop-blur text-white text-xs font-semibold px-2 py-0.5 rounded-full">
            <Sparkles className="w-3 h-3" />
            {Math.round(score * 100)}% match
          </span>
        )}
      </div>

      {/* Meta */}
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold text-white line-clamp-2 group-hover:text-brand-400 transition-colors">
          {video.title}
        </h3>

        <p className="text-xs text-zinc-400 font-medium">{video.channel}</p>

        <div className="flex items-center gap-2 text-xs text-zinc-500">
          {video.viewCount && (
            <span className="flex items-center gap-1">
              <Eye className="w-3 h-3" />
              {formatViews(video.viewCount)}
            </span>
          )}
          {video.publishedAt && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {timeAgo(video.publishedAt)}
            </span>
          )}
        </div>

        {/* AI match reason */}
        {video.matchReason && (
          <p className="text-xs text-brand-400 mt-0.5 line-clamp-1">
            ✦ {video.matchReason}
          </p>
        )}
      </div>
    </Link>
  );
}
