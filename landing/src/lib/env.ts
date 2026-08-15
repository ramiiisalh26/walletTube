/** Runtime config sourced from Vite env vars, with safe fallbacks. */

export const WAITLIST_URL: string = import.meta.env.VITE_WAITLIST_URL ?? "";
export const EXTENSION_URL: string = import.meta.env.VITE_EXTENSION_URL || "#";

/** True when a real waitlist endpoint is configured. */
export const HAS_WAITLIST_BACKEND: boolean = WAITLIST_URL.trim().length > 0;
