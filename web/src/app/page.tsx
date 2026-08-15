import Link from "next/link";
import { SearchBar } from "@/components/SearchBar";

const EXAMPLES = [
  "how does JWT refresh work",
  "explain the event loop in node",
  "react useEffect cleanup function",
  "what is a database index",
];

export default function HomePage() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center px-4 pt-20 text-center sm:pt-28">
      <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
        Find the <span className="text-brand">exact moment</span>
        <br />
        it&apos;s explained.
      </h1>
      <p className="mt-4 max-w-xl text-zinc-400">
        Stop scrubbing through hour-long tutorials. Search across YouTube
        transcripts and jump straight to the second a concept is covered.
      </p>

      <div className="mt-8 w-full">
        <SearchBar autoFocus />
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        <span className="text-sm text-zinc-500">Try:</span>
        {EXAMPLES.map((ex) => (
          <Link
            key={ex}
            href={`/search?q=${encodeURIComponent(ex)}`}
            className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-sm text-zinc-300 transition-colors hover:border-zinc-600"
          >
            {ex}
          </Link>
        ))}
      </div>
    </div>
  );
}
