/**
 * Types mirror the FastAPI backend exactly.
 * Source of truth: python/api/schemas/search.py and python/api/schemas/user.py
 * Keep field names snake_case to match the JSON the API returns.
 */

export interface SearchRequest {
  query: string;
  industry?: string | null;
  page?: number; // default 0
  size?: number; // default 10, max 50
  session_id?: string | null;
  referrer_clip_slug?: string | null;
  no_cache?: boolean;
}

/** Surrounding ~2-minute context span for a hit (small-to-big retrieval). */
export interface ParentContext {
  text: string;
  start_time: number;
  end_time: number;
}

export interface SearchResult {
  video_id: string; // YouTube video id (e.g. "dQw4w9WgXcQ")
  title: string;
  thumbnail_url: string | null;
  channel_name: string | null;
  text: string; // matched transcript chunk
  start_time: number; // seconds into the video
  end_time: number;
  view_count: number;
  similarity: number; // 0–1 relevance score
  youtube_url: string; // watch page, deep-linked to the timestamp
  embed_url: string; // embeddable player starting at the timestamp
  context: ParentContext | null;
  // Stage 5 (cross-encoder) fields — null when reranking was skipped
  bi_score: number | null;
  cross_score: number | null;
  parent_chunk_start: number | null;
  segment_start: number | null;
  segment_end: number | null;
}

export interface SearchResponse {
  query: string;
  total_results: number;
  source: string;
  latency_ms: number;
  indexing_more: boolean;
  detected_industry: string | null;
  results: SearchResult[];
  search_event_id: number | null;
  refined: boolean;
  // Free-tier (anonymous) usage — null for logged-in users.
  free_searches_used: number | null;
  free_searches_limit: number | null;
  free_searches_remaining: number | null;
}

export interface ClickRequest {
  search_id: number;
  video_id: number; // NOTE: backend expects the DB integer id, not the YouTube id
  chunk_id?: number | null;
  position: number;
}
