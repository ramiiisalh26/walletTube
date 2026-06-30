import type { Metadata } from "next";
import "./globals.css";
import Navbar from "@/components/Navbar";

export const metadata: Metadata = {
  title: "Bucket Tube — AI-Powered YouTube Search",
  description:
    "Find exactly what you're looking for across millions of YouTube videos using AI semantic search.",
  openGraph: {
    title: "Bucket Tube",
    description: "AI-powered YouTube search engine",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface text-white antialiased">
        <Navbar />
        <main>{children}</main>
      </body>
    </html>
  );
}
