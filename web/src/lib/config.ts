/**
 * Master switch for the upgrade / billing UI. Off by default — flip by setting
 * NEXT_PUBLIC_BILLING_ENABLED=true in .env.local (and billing_enabled=True in
 * python/config.py) when ready to charge.
 */
export const BILLING_ENABLED =
  process.env.NEXT_PUBLIC_BILLING_ENABLED === "true";
