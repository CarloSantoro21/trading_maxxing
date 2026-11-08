"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/components/auth/AuthProvider";
import { secondaryButtonClass } from "@/components/ui/styles";

/** Páginas de la app con sesión (el orden es el de la barra). */
export const NAV_LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/strategies", label: "Estrategias" },
  { href: "/onboarding", label: "Mi perfil" },
] as const;

/**
 * Encabezado común de las páginas con sesión: navegación y cierre de sesión.
 * Va dentro de <RequireAuth>.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { supabase, session } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  async function handleSignOut() {
    await supabase?.auth.signOut();
    router.replace("/login");
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 bg-white/80 backdrop-blur dark:border-neutral-800 dark:bg-neutral-950/80">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/dashboard" className="font-semibold tracking-tight">
            trading_maxxing
          </Link>
          <nav aria-label="Principal" className="order-3 w-full sm:order-none sm:w-auto">
            <ul className="flex gap-1 overflow-x-auto text-sm">
              {NAV_LINKS.map((link) => {
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
          <div className="ml-auto flex items-center gap-3">
            {session?.user.email && (
              <span className="hidden text-sm text-neutral-500 md:inline">{session.user.email}</span>
            )}
            <button type="button" className={`${secondaryButtonClass} px-3 py-1.5`} onClick={handleSignOut}>
              Cerrar sesión
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:py-8">{children}</main>
    </div>
  );
}
