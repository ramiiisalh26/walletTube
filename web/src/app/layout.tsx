import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import { Youtube } from "lucide-react";
import { BILLING_ENABLED } from "@/lib/config";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "YTSearch — Find the exact moment in any YouTube video",
  description:
    "Semantic search across YouTube transcripts. Skip the scrubbing — jump straight to the second a concept is explained.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen font-sans">
        <header className="border-b border-zinc-800">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold">
              <Youtube className="h-6 w-6 text-brand" />
              <span>YTSearch</span>
            </Link>
            <nav className="flex items-center gap-5 text-sm text-zinc-400">
              <Link href="/search" className="hover:text-zinc-100">
                Search
              </Link>
              {BILLING_ENABLED && (
                <Link href="/pricing" className="hover:text-zinc-100">
                  Pricing
                </Link>
              )}
            </nav>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
