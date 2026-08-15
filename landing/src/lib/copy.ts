/**
 * Single source of truth for every string on the page.
 * Edit anything here — no need to touch component files.
 */

export const brand = {
  name: "Recall",
  mission: "Searchable memory for how to do anything.",
};

export const hero = {
  eyebrow: "The searchable memory for how to do anything",
  headline: "Find the exact moment anyone explained it.",
  subhead:
    "Human knowledge is trapped inside millions of hours of video. Recall makes it searchable to the second — so you land on the precise moment a real person shows you how.",
  primaryCta: "Get early access",
  secondaryCta: "See it work",
  // Cycled through the fake search bar to show the engine is universal.
  queries: [
    { field: "Programming", text: "how to fix a NullPointerException", timestamp: "07:42", source: "Java Crash Course" },
    { field: "Music", text: "how to change a guitar chord smoothly", timestamp: "12:15", source: "Guitar Basics" },
    { field: "Programming", text: "how to debug a memory leak", timestamp: "22:37", source: "Profiling Deep Dive" },
    { field: "Cooking", text: "how to knead sourdough by hand", timestamp: "04:08", source: "Artisan Bread at Home" },
    { field: "Math", text: "how to solve a limit at infinity", timestamp: "16:51", source: "Calculus, Explained" },
  ],
};

export const problem = {
  heading: "You saw it explained perfectly. Now you can't find it.",
  body: "Somewhere in a 40-minute video, someone showed you exactly how. But which video? And where in it? Scrubbing back and forth isn't search — it's guessing.",
  personas: [
    { emoji: "💻", label: "The developer", line: "“That one video fixed my exact bug… last month.”" },
    { emoji: "🎸", label: "The musician", line: "“The teacher showed the finger position somewhere.”" },
    { emoji: "🎓", label: "The student", line: "“The professor proved it — I just can’t find the step.”" },
    { emoji: "🍳", label: "The home cook", line: "“They demoed the fold, but which minute was it?”" },
  ],
};

export const howItWorks = {
  heading: "Ask in plain language. Land on the moment.",
  subhead: "No keywords. No scrubbing. Just the answer, timestamped.",
  steps: [
    {
      n: "01",
      title: "Ask like a human",
      body: "Type a question, describe what you need, or paste the exact error you're staring at.",
    },
    {
      n: "02",
      title: "It searches inside the videos",
      body: "Recall reads every spoken word across thousands of videos — and soon, the on-screen code and text too. Not titles. The content.",
    },
    {
      n: "03",
      title: "Jump to the exact 5 seconds",
      body: "Land on the precise moment a real person does it — watchable, verifiable, no scrubbing required.",
    },
  ],
};

export const demo = {
  badge: "Available now for programming",
  heading: "Paste the error. Jump to the fix.",
  subhead:
    "For developers, this is live today. Recall turns a stack trace into the exact moment someone solved it on video — and always gives you an answer, even when no clip exists yet.",
  query: "TypeError: Cannot read properties of undefined (reading 'map')",
  results: [
    {
      title: "React Rendering Lists — Common Mistakes",
      channel: "Frontend Foundations",
      timestamp: "08:14",
      snippet:
        "…this crashes because data is undefined on the first render — guard it with data?.map or default it to an empty array…",
    },
    {
      title: "Debugging 'undefined is not a function' in JS",
      channel: "The Debugger",
      timestamp: "03:52",
      snippet:
        "…the array hasn't loaded yet, so .map runs on undefined. Here's the fix using optional chaining…",
    },
    {
      title: "Async Data + useEffect, Explained",
      channel: "Hooks in Depth",
      timestamp: "19:07",
      snippet:
        "…initialize state to [] so the first render maps over an empty array instead of undefined…",
    },
  ],
  aiAnswer: {
    label: "No video moment yet — here's an AI answer",
    body: "Your data is undefined on the first render, before it loads. Initialize state to an empty array (useState([])) or guard the call with optional chaining: data?.map(...). This makes the first render safe while the real data arrives.",
  },
  disclaimer: "Available now for programming. More fields coming.",
};

export const audiences = {
  heading: "Built for anyone learning by watching.",
  subhead:
    "One engine, every field. Programming is live now — the rest are on the way.",
  fields: [
    { icon: "Code2", label: "Developers", example: "“fix a CORS error in Express”", live: true },
    { icon: "GraduationCap", label: "Students", example: "“prove the Pythagorean theorem”" },
    { icon: "Music", label: "Musicians", example: "“barre chord without buzzing”" },
    { icon: "ChefHat", label: "Cooks", example: "“temper chocolate properly”" },
    { icon: "Dumbbell", label: "Fitness", example: "“fix my deadlift form”" },
    { icon: "Stethoscope", label: "Medicine", example: "“tie a surgical suture knot”" },
    { icon: "Wrench", label: "Trades", example: "“replace a brake caliper”" },
    { icon: "Languages", label: "Languages", example: "“roll the Spanish R”" },
    { icon: "PenTool", label: "Designers", example: "“mask an image in Figma”" },
  ],
};

export const whyDifferent = {
  heading: "Proof when it exists. An answer when it doesn't.",
  subhead: "Never a dead end.",
  columns: [
    {
      title: "Generic video search",
      tone: "muted" as const,
      points: [
        "Matches titles and tags, not content",
        "Returns whole 40-minute videos",
        "You still have to scrub to find it",
      ],
    },
    {
      title: "AI chat",
      tone: "muted" as const,
      points: [
        "Gives text that might be confidently wrong",
        "No proof, nothing to watch",
        "Can't show you a technique in motion",
      ],
    },
    {
      title: "Recall",
      tone: "accent" as const,
      points: [
        "Searches every spoken word inside videos",
        "Lands on the exact 5-second moment",
        "A real human, verified and watchable",
        "An AI answer when no clip exists yet",
      ],
    },
  ],
};

export const waitlist = {
  heading: "Human knowledge shouldn't be locked inside a 40-minute video.",
  subhead:
    "Get early access as Recall opens up, field by field. We'll start you with programming.",
  placeholder: "you@example.com",
  cta: "Get early access",
  extensionCta: "Install the extension",
  successTitle: "You're on the list.",
  successBody: "We'll email you the moment your field goes live.",
  errorText: "Something went wrong. Please try again.",
  invalidEmail: "Please enter a valid email address.",
  socialProofLabel: "As seen on",
  socialProof: ["Product Hunt", "Hacker News", "Reddit"],
};

export const footer = {
  tagline: "The searchable memory for how to do anything.",
  links: [
    { label: "About", href: "#" },
    { label: "Privacy", href: "#" },
    { label: "Contact", href: "#" },
  ],
  socials: [
    { label: "X / Twitter", icon: "Twitter", href: "#" },
    { label: "GitHub", icon: "Github", href: "#" },
    { label: "YouTube", icon: "Youtube", href: "#" },
  ],
  copyright: `© ${new Date().getFullYear()} ${brand.name}. All rights reserved.`,
};
