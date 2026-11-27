"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LOGOUT_URL, useMe } from "@/components/auth/RequireAuth";
import { secondaryButtonClass } from "@/components/ui/styles";
import { useEngineStatus } from "@/lib/engine";
import { appEnv, commitSha } from "@/lib/env";
import { formatDateTime } from "@/lib/format";

/** Páginas de la app con sesión (el orden es el de la barra). */
export const NAV_LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/strategies", label: "Estrategias" },
  { href: "/backtests", label: "Backtests" },
  { href: "/goals", label: "Metas" },
  { href: "/onboarding", label: "Mi perfil" },
] as const;

export const ADMIN_LINK = { href: "/admin", label: "Admin" } as const;

const ENV_STYLE = {
  local: "bg-neutral-100 text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
  dev: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  prod: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
} as const;

/** Ambiente y commit del build (NEXT_PUBLIC_ENV y NEXT_PUBLIC_COMMIT_SHA). */
export function EnvBadge() {
  const env = appEnv();
  const sha = commitSha();
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-xs font-medium ${ENV_STYLE[env]}`}
      title="Ambiente y versión del frontend"
      data-testid="env-badge"
    >
      {env}
      <span aria-hidden="true">·</span>
      {sha ?? "sin versión"}
    </span>
  );
}

/** "Motor en línea / desconectado" según el último latido del motor (GET /api/engine/status). */
export function EngineIndicator() {
  const { data, state } = useEngineStatus();
  // Sin llaves de Binance Testnet el engine ejecuta simulado (fills al precio público).
  const label =
    state.kind === "loading"
      ? "Motor: consultando…"
      : state.kind === "ready"
        ? state.sim
          ? "Motor en línea (simulado)"
          : "Motor en línea"
        : state.kind === "partial"
          ? `Motor incompleto (falta ${state.missing.join(" y ")})`
          : "Motor desconectado";
  const dot =
    state.kind === "ready" ? "bg-emerald-500" : state.kind === "partial" ? "bg-amber-500" : state.kind === "offline" ? "bg-red-500" : "bg-neutral-400";
  const title = data?.last_heartbeat_at
    ? `Último latido: ${formatDateTime(data.last_heartbeat_at)}`
    : "El motor de la Mac no ha mandado latidos";
  return (
    <span aria-live="polite" title={title} data-testid="engine-indicator" className="inline-flex items-center gap-1.5 text-xs text-neutral-600 dark:text-neutral-300">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

/**
 * Encabezado común de las páginas con sesión: navegación, estado del motor, ambiente y
 * versión, y cierre de sesión (logout de Cloudflare Access). Va dentro de <RequireAuth>.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, isAdmin } = useMe();
  const pathname = usePathname();
  const links = isAdmin ? [...NAV_LINKS, ADMIN_LINK] : NAV_LINKS;

  return (
    <div className="min-h-screen bg-neutral-50 dark:bg-neutral-950">
      <header className="sticky top-0 z-30 border-b border-neutral-200/80 bg-white/70 shadow-sm backdrop-blur-md supports-[backdrop-filter]:bg-white/60 dark:border-neutral-800/80 dark:bg-neutral-950/70 supports-[backdrop-filter]:dark:bg-neutral-950/60">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link
            href="/dashboard"
            aria-label="Ir al dashboard"
            className="group flex items-center gap-2.5 font-semibold tracking-tight transition-opacity hover:opacity-80"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/logo.png"
              alt=""
              aria-hidden="true"
              className="h-9 w-9 rounded-md shadow-sm ring-1 ring-black/5 transition-transform group-hover:scale-105 dark:ring-white/10"
            />
            <span>trading_maxxing</span>
          </Link>
          <nav aria-label="Principal" className="order-3 w-full lg:order-none lg:w-auto">
            <ul className="flex gap-1 overflow-x-auto text-sm">
              {links.map((link) => {
                const active = pathname === link.href;
                return (
                  <li key={link.href}>
                    <Link
                      href={link.href}
                      aria-current={active ? "page" : undefined}
                      className={`block rounded-md px-3 py-1.5 whitespace-nowrap transition-colors ${
                        active
                          ? "bg-emerald-50 font-semibold text-emerald-900 ring-1 ring-emerald-200 dark:bg-emerald-950/70 dark:text-emerald-200 dark:ring-emerald-900/70"
                          : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
                      }`}
                    >
                      {link.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <div className="inline-flex items-center gap-2.5 rounded-full border border-neutral-200/70 bg-white/60 px-3 py-1 shadow-sm dark:border-neutral-800/70 dark:bg-neutral-900/60">
              <EngineIndicator />
              <span aria-hidden="true" className="h-3.5 w-px bg-neutral-200 dark:bg-neutral-700" />
              <EnvBadge />
            </div>
            <span
              title={me.email}
              className="hidden max-w-[180px] truncate text-sm text-neutral-500 md:inline"
            >
              {me.email}
            </span>
            <a href={LOGOUT_URL} className={`${secondaryButtonClass} px-3 py-1.5`}>
              Cerrar sesión
            </a>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:py-8">{children}</main>
    </div>
  );
}
