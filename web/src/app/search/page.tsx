import { Suspense } from "react";
import { Loader2 } from "lucide-react";
import { SearchView } from "@/components/SearchView";

// useSearchParams() must be wrapped in a Suspense boundary in the App Router.
export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-20 text-zinc-500">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      }
    >
      <SearchView />
    </Suspense>
  );
}
