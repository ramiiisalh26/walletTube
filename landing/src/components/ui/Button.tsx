import type { AnchorHTMLAttributes, PropsWithChildren } from "react";

type Variant = "primary" | "secondary";

interface ButtonProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  variant?: Variant;
}

const base =
  "inline-flex items-center justify-center gap-2 rounded-full px-6 py-3 text-sm font-semibold transition-all duration-200 focus-visible:outline-none";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-white shadow-glow hover:bg-accent-600 hover:-translate-y-0.5",
  secondary:
    "border border-line bg-surface/60 text-zinc-200 hover:border-accent/60 hover:text-white",
};

/** Anchor-based button so CTAs can smooth-scroll to sections by href. */
export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: PropsWithChildren<ButtonProps>) {
  return (
    <a className={`${base} ${variants[variant]} ${className}`} {...props}>
      {children}
    </a>
  );
}
