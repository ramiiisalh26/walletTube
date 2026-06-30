"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Search } from "lucide-react";
import { useState, useEffect, Suspense } from "react";

function NavSearch() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [value, setValue] = useState(searchParams.get("q") ?? "");

  useEffect(() => {
    setValue(searchParams.get("q") ?? "");
  }, [searchParams]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = value.trim();
    if (!q) return;
    router.push(`/search?q=${encodeURIComponent(q)}`);
  }

  return (
    <form onSubmit={handleSubmit} className="flex-1 max-w-xl">
      <div className="relative flex items-center
        bg-white/[0.04] border border-white/[0.08]
        hover:border-white/[0.15] focus-within:border-brand-600/50
        focus-within:shadow-[0_0_0_2px_rgba(220,38,38,0.10)]
        rounded-full transition-all duration-200">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Search with AI…"
          className="w-full bg-transparent text-white placeholder:text-white/25
            text-sm pl-4 pr-12 py-2.5 outline-none rounded-full"
        />
        <button
          type="submit"
          className="absolute right-1.5 bg-brand-600 hover:bg-brand-500
            rounded-full p-2 transition-colors"
        >
          <Search className="w-3.5 h-3.5 text-white" />
        </button>
      </div>
    </form>
  );
}

export default function Navbar() {
  return (
    <nav className="sticky top-0 z-50
      bg-[#080808]/80 backdrop-blur-xl
      border-b border-white/[0.06]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-5">
        <Link href="/" className="flex items-center gap-0.5 shrink-0">
          <span className="text-brand-500 font-bold text-lg tracking-tight">Bucket</span>
          <span className="text-white  font-bold text-lg tracking-tight">Tube</span>
        </Link>

        <Suspense fallback={null}>
          <NavSearch />
        </Suspense>
      </div>
    </nav>
  );
}
