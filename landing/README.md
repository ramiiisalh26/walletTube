# Recall — Landing Page

A single-page marketing site for launch traffic (Product Hunt / Hacker News /
Reddit). Opens with the universal vision, narrows to the live programming demo,
and converts visitors to the waitlist.

Built with **React + Vite + TypeScript + Tailwind CSS + Framer Motion**.

## Run it

```bash
cd landing
npm install
npm run dev
```

Open the URL Vite prints (default http://localhost:5173).

Other scripts:

```bash
npm run build     # type-check + production build to dist/
npm run preview   # preview the production build locally
```

## Configuration

Copy `.env.example` to `.env` and set:

| Variable             | Purpose                                                        | Fallback              |
| -------------------- | ------------------------------------------------------------- | --------------------- |
| `VITE_WAITLIST_URL`  | Endpoint the waitlist form `POST`s `{ email }` to.            | none → **demo mode**  |
| `VITE_EXTENSION_URL` | Link for the "Install the extension" button.                  | `#`                   |

**Demo mode:** when `VITE_WAITLIST_URL` is empty, the form still validates the
email and shows the success animation without calling any server — safe for a
static deploy before the backend exists.

## Editing copy

**All marketing text lives in [`src/lib/copy.ts`](src/lib/copy.ts).** Headlines,
subheads, the cycling hero queries, demo results, audience cards, comparison
points, and footer links are all there — edit that one file, no component
changes needed.

The brand name/logo is a placeholder: change `brand.name` in `copy.ts` and swap
the SVG in [`src/components/ui/Wordmark.tsx`](src/components/ui/Wordmark.tsx).

## Structure

```
src/
  components/
    Hero.tsx              # big vision + cycling fake search bar
    Problem.tsx           # relatable pain, persona cards
    HowItWorks.tsx        # 3 animated steps
    Demo.tsx              # programming demo: paste error → results + AI fallback
    Audiences.tsx         # expansion-vision field grid
    WhyDifferent.tsx      # vs. search / vs. AI chat comparison
    Waitlist.tsx          # email capture + success animation
    Footer.tsx
    ui/                   # Container, Section, SectionHeading, Reveal, Button,
                          # Wordmark, ConvergingBackground
  hooks/
    useRotatingIndex.ts   # drives the hero query cycling
  lib/
    copy.ts               # ALL text
    env.ts                # env vars + fallbacks
  App.tsx                 # composes sections (below-fold are lazy-mounted)
  main.tsx
```

## Accessibility & performance

- **Reduced motion:** every animation checks `prefers-reduced-motion` and renders
  the final state with no movement.
- **Semantic HTML:** header/main/section/footer, real headings, labelled form
  input, `aria-live` status messages, keyboard-navigable, visible focus rings.
- **Fast:** below-the-fold sections are code-split via `React.lazy`; the hero
  particle animation is skipped entirely under reduced motion.

## Accent color

One accent (violet) is used throughout, defined once in
[`tailwind.config.ts`](tailwind.config.ts) as `accent`. Change it there to
re-theme the whole page.
