"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { LOGOUT_URL, useMe } from "@/components/auth/RequireAuth";
import { SLOW_POLL_MS } from "@/components/providers/ApiProvider";
import { secondaryButtonClass } from "@/components/ui/styles";
import type { EngineStatus } from "@/lib/api/client";
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
  const { data, error } = useSWR<EngineStatus>("/engine/status", { refreshInterval: SLOW_POLL_MS });
  const known = data !== undefined;
  const online = Boolean(data?.online) && !error;
  const label = !known && !error ? "Motor: consultando…" : online ? "Motor en línea" : "Motor desconectado";
  const title = data?.last_heartbeat_at
    ? `Último latido: ${formatDateTime(data.last_heartbeat_at)}`
    : "El motor de la Mac no ha mandado latidos";
  return (
    <span aria-live="polite" title={title} data-testid="engine-indicator" className="inline-flex items-center gap-1.5 text-xs text-neutral-600 dark:text-neutral-300">
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${online ? "bg-emerald-500" : known || error ? "bg-red-500" : "bg-neutral-400"}`}
      />
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
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/80">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/dashboard" className="font-semibold tracking-tight">
            trading_maxxing
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
                      className={`block rounded-md px-3 py-1.5 whitespace-nowrap ${
                        active
                          ? "bg-emerald-50 font-semibold text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
                          : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-900"
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
            <EngineIndicator />
            <EnvBadge />
            <span className="hidden text-sm text-neutral-500 md:inline">{me.email}</span>
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
