import { lazy, Suspense } from "react";
import { Hero } from "./components/Hero";

// Below-the-fold sections are code-split and mounted lazily to keep the
// initial hero paint fast.
const Problem = lazy(() =>
  import("./components/Problem").then((m) => ({ default: m.Problem })),
);
const HowItWorks = lazy(() =>
  import("./components/HowItWorks").then((m) => ({ default: m.HowItWorks })),
);
const Demo = lazy(() =>
  import("./components/Demo").then((m) => ({ default: m.Demo })),
);
const Audiences = lazy(() =>
  import("./components/Audiences").then((m) => ({ default: m.Audiences })),
);
const WhyDifferent = lazy(() =>
  import("./components/WhyDifferent").then((m) => ({ default: m.WhyDifferent })),
);
const Waitlist = lazy(() =>
  import("./components/Waitlist").then((m) => ({ default: m.Waitlist })),
);
const Footer = lazy(() =>
  import("./components/Footer").then((m) => ({ default: m.Footer })),
);

export default function App() {
  return (
    <div className="min-h-screen">
      <Hero />
      <Suspense fallback={<div className="h-40" aria-hidden="true" />}>
        <main>
          <Problem />
          <HowItWorks />
          <Demo />
          <Audiences />
          <WhyDifferent />
          <Waitlist />
        </main>
        <Footer />
      </Suspense>
    </div>
  );
}
