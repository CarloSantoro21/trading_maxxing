import Link from "next/link";

/** Navegación mínima de las secciones de análisis (/backtests y /goals). */
const LINKS = [
  { href: "/", label: "Inicio" },
  { href: "/backtests", label: "Backtests" },
  { href: "/goals", label: "Metas" },
] as const;

export function SectionNav({ current }: { current: string }) {
  return (
    <nav aria-label="Secciones" className="mb-6 flex flex-wrap gap-4 text-sm">
      {LINKS.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          aria-current={l.href === current ? "page" : undefined}
          className={
            l.href === current
              ? "font-semibold text-neutral-900 dark:text-neutral-100"
              : "text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
          }
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
