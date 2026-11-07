"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "./AuthProvider";

/**
 * Protección de rutas del lado del cliente.
 *
 * Como el sitio es estático no hay middleware: si no hay sesión se redirige a /login
 * con `next` para volver después. La seguridad real la dan las políticas RLS; esto
 * solo evita mostrar pantallas que no funcionarían sin sesión.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { session, loading, configError } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !configError && !session) {
      router.replace(`/login?next=${encodeURIComponent(pathname || "/")}`);
    }
  }, [loading, configError, session, router, pathname]);

  if (configError) {
    return (
      <p role="alert" className="mx-auto max-w-xl p-8 text-red-700 dark:text-red-400">
        {configError}
      </p>
    );
  }

  if (loading || !session) {
    return (
      <p role="status" aria-live="polite" className="mx-auto max-w-xl p-8 text-neutral-500">
        Verificando sesión…
      </p>
    );
  }

  return <>{children}</>;
}

/** Acepta solo rutas internas para evitar redirecciones abiertas. */
export function safeNextPath(next: string | null | undefined, fallback = "/onboarding"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return fallback;
  }
  return next;
}
