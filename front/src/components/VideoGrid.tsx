import VideoCard from "./VideoCard";
import type { Video } from "@/types";

interface Props {
  videos: Video[];
  loading?: boolean;
}

function SkeletonCard() {
  return (
    <div className="flex flex-col gap-3 animate-pulse">
      <div className="aspect-video rounded-xl bg-surface-elevated" />
      <div className="space-y-2">
        <div className="h-3.5 bg-surface-elevated rounded w-3/4" />
        <div className="h-3 bg-surface-elevated rounded w-1/2" />
        <div className="h-3 bg-surface-elevated rounded w-1/3" />
      </div>
    </div>
  );
}

export default function VideoGrid({ videos, loading }: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
        {Array.from({ length: 12 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    );
  }

  if (!videos.length) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <p className="text-4xl mb-4">🪣</p>
        <p className="text-zinc-400 text-lg font-medium">No videos found</p>
        <p className="text-zinc-600 text-sm mt-1">
          Try different keywords or a broader query
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
      {videos.map((v) => (
        <VideoCard key={v.id} video={v} />
      ))}
    </div>
  );
}
