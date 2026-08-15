"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { ApiError, createCheckout } from "@/lib/api";

const FREE_FEATURES = [
  "3 searches per day",
  "Jump to exact timestamps",
  "No account required",
];

const PRO_FEATURES = [
  "Unlimited searches",
  "Full search history",
  "Saved clips & collections",
  "Priority results",
];

export function PricingPlans() {
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleUpgrade() {
    const priceId = process.env.NEXT_PUBLIC_STRIPE_PRO_PRICE_ID;
    if (!priceId) {
      setStatus("Stripe is not configured yet (set NEXT_PUBLIC_STRIPE_PRO_PRICE_ID).");
      return;
    }

    setLoading(true);
    setStatus(null);
    try {
      const { url } = await createCheckout(priceId);
      window.location.href = url;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setStatus("Please sign in to upgrade. (Login is coming soon.)");
      } else {
        setStatus("Could not start checkout. Please try again.");
      }
      setLoading(false);
    }
  }

  return (
    <div className="grid gap-6 sm:grid-cols-2">
      {/* Free */}
      <div className="flex flex-col rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
        <h2 className="text-lg font-semibold">Free</h2>
        <p className="mt-1 text-sm text-zinc-400">For trying it out</p>
        <p className="mt-4 text-3xl font-bold">
          $0<span className="text-base font-normal text-zinc-500">/mo</span>
        </p>
        <ul className="mt-6 flex flex-col gap-3 text-sm">
          {FREE_FEATURES.map((f) => (
            <li key={f} className="flex items-center gap-2 text-zinc-300">
              <Check className="h-4 w-4 text-zinc-500" />
              {f}
            </li>
          ))}
        </ul>
        <div className="mt-6 rounded-full border border-zinc-700 px-6 py-2.5 text-center text-sm text-zinc-400">
          Current plan
        </div>
      </div>

      {/* Pro */}
      <div className="flex flex-col rounded-2xl border border-brand/50 bg-brand/5 p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Pro</h2>
          <span className="rounded-full bg-brand px-2.5 py-0.5 text-xs font-medium text-white">
            Popular
          </span>
        </div>
        <p className="mt-1 text-sm text-zinc-400">For daily learners</p>
        <p className="mt-4 text-3xl font-bold">
          $19<span className="text-base font-normal text-zinc-500">/mo</span>
        </p>
        <ul className="mt-6 flex flex-col gap-3 text-sm">
          {PRO_FEATURES.map((f) => (
            <li key={f} className="flex items-center gap-2 text-zinc-100">
              <Check className="h-4 w-4 text-brand" />
              {f}
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={handleUpgrade}
          disabled={loading}
          className="mt-6 rounded-full bg-brand px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-brand-hover disabled:opacity-60"
        >
          {loading ? "Starting checkout…" : "Upgrade to Pro"}
        </button>
        {status && (
          <p className="mt-3 text-center text-xs text-zinc-400">{status}</p>
        )}
      </div>
    </div>
  );
}
