import type { LucideIcon } from "lucide-react";
import { Twitter, Github, Youtube } from "lucide-react";
import { Container } from "./ui/Container";
import { Wordmark } from "./ui/Wordmark";
import { footer } from "../lib/copy";

const SOCIAL_ICONS: Record<string, LucideIcon> = { Twitter, Github, Youtube };

export function Footer() {
  return (
    <footer className="border-t border-line py-12">
      <Container>
        <div className="flex flex-col items-center gap-8 sm:flex-row sm:items-start sm:justify-between">
          <div className="text-center sm:text-left">
            <Wordmark />
            <p className="mt-3 max-w-xs text-sm text-zinc-500">
              {footer.tagline}
            </p>
          </div>

          <nav className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
            {footer.links.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="text-sm text-zinc-400 transition-colors hover:text-white"
              >
                {link.label}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-4">
            {footer.socials.map((social) => {
              const Icon = SOCIAL_ICONS[social.icon] ?? Twitter;
              return (
                <a
                  key={social.label}
                  href={social.href}
                  aria-label={social.label}
                  className="text-zinc-500 transition-colors hover:text-accent-300"
                >
                  <Icon className="h-5 w-5" />
                </a>
              );
            })}
          </div>
        </div>

        <p className="mt-10 text-center text-xs text-zinc-600">
          {footer.copyright}
        </p>
      </Container>
    </footer>
  );
}
