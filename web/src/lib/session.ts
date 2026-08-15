/**
 * Anonymous session id — lets the backend group searches from the same browser
 * before the user has an account. Stored in localStorage so it survives reloads.
 */
const SESSION_KEY = "yts_session_id";

export function getSessionId(): string {
  if (typeof window === "undefined") return "";
  let id = window.localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}
