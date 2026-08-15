import type { Metadata } from "next";
import Link from "next/link";
import { PricingPlans } from "@/components/PricingPlans";
import { BILLING_ENABLED } from "@/lib/config";

export const metadata: Metadata = {
  title: "Pricing — YTSearch",
  description: "Simple pricing. Start free, upgrade when you need unlimited searches.",
};

export default function PricingPage() {
  if (!BILLING_ENABLED) {
    return (
      <div className="mx-auto max-w-xl px-4 py-24 text-center">
        <h1 className="text-2xl font-bold tracking-tight">Pricing coming soon</h1>
        <p className="mt-3 text-zinc-400">
          Everything is free while we&apos;re in early access — search as much as
          you like.
        </p>
        <Link
          href="/search"
          className="mt-6 inline-block rounded-full bg-brand px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-brand-hover"
        >
          Start searching
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-16">
      <div className="mb-10 text-center">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Simple, honest pricing
        </h1>
        <p className="mt-3 text-zinc-400">
          Start free, no account needed. Upgrade when you want unlimited
          searches.
        </p>
      </div>
      <PricingPlans />
    </div>
  );
}
