import type { SearchResponse, VideoDetail } from "@/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }

  return res.json() as Promise<T>;
}

/** Search videos via the AI backend */
export async function searchVideos(
  query: string,
  page = 1,
  pageSize = 20
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q: query,
    page: String(page),
    page_size: String(pageSize),
  });
  return apiFetch<SearchResponse>(`/search?${params}`);
}

/** Fetch a single video's detail + AI enrichment */
export async function getVideo(id: string): Promise<VideoDetail> {
  return apiFetch<VideoDetail>(`/videos/${id}`);
}
