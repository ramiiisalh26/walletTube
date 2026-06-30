export interface Video {
  id: string;
  title: string;
  channel: string;
  channelId?: string;
  thumbnail: string;
  duration: string;        // "12:34"
  publishedAt: string;     // ISO date string
  viewCount?: number;
  description?: string;
  relevanceScore?: number; // 0–1 from the AI backend
  matchReason?: string;    // e.g. "Mentions 'RAG pipeline' at 4:20"
}

export interface SearchResponse {
  query: string;
  results: Video[];
  total: number;
  processingTimeMs: number;
}

export interface VideoDetail extends Video {
  transcript?: TranscriptSegment[];
  aiSummary?: string;
  keyMoments?: KeyMoment[];
  tags?: string[];
}

export interface TranscriptSegment {
  startSec: number;
  text: string;
}

export interface KeyMoment {
  startSec: number;
  label: string;
}
