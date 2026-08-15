import type {
  ClickRequest,
  SearchRequest,
  SearchResponse,
} from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
const TOKEN_KEY = "yts_access_token";

/** Thrown for any non-2xx response so callers can branch on `.status`. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = window.localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    // The API wraps errors as { "error": "..." }; fall back to status text.
    const message = await res
      .json()
      .then((body) => body.error ?? body.detail ?? res.statusText)
      .catch(() => res.statusText);
    throw new ApiError(res.status, message);
  }

  return res.json() as Promise<T>;
}

/** POST /api/search — primary semantic search. */
export function searchVideos(req: SearchRequest): Promise<SearchResponse> {
  return apiFetch<SearchResponse>("/api/search", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/**
 * POST /api/search/click — re-ranking signal.
 * Requires the DB integer video id + chunk id, which the search response does
 * not currently expose; wire this up once the backend includes those ids.
 */
export function recordClick(req: ClickRequest): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/api/search/click", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/**
 * POST /api/billing/checkout — start a Stripe Checkout session.
 * Requires authentication (Bearer token); throws ApiError(401) when signed out.
 */
export function createCheckout(priceId: string): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/api/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ price_id: priceId }),
  });
}
