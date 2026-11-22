"use client";

import { createContext, useContext } from "react";
import useSWR, { type KeyedMutator } from "swr";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { ApiError, apiErrorMessage, type Me } from "@/lib/api/client";

/**
 * Sesión del usuario.
 *
 * El login lo hace Cloudflare Access antes de que cargue el sitio (One-time PIN por
 * correo), así que aquí no hay formulario: la app pide `GET /api/me` y la API valida el JWT
 * de Access (en local, con `AUTH_MODE=dev`, devuelve el usuario demo). Si la API responde
 * 401/403 el correo no está dado de alta y se muestra una pantalla para pedir acceso.
 */

export const ME_KEY = "/me";
/** Cierra la sesión de Cloudflare Access (borra la cookie `CF_Authorization`). */
export const LOGOUT_URL = "/cdn-cgi/access/logout";

interface MeContextValue {
  me: Me;
  isAdmin: boolean;
  /** Vuelve a leer `/api/me` (p. ej. después de guardar el perfil). */
  refresh: KeyedMutator<Me>;
}

const MeContext = createContext<MeContextValue | null>(null);

/** Usuario actual. Solo dentro de <RequireAuth>, que garantiza que ya se cargó. */
export function useMe(): MeContextValue {
  const ctx = useContext(MeContext);
  if (!ctx) throw new Error("useMe debe usarse dentro de <RequireAuth>.");
  return ctx;
}

export function AccessDenied({ detail }: { detail?: string }) {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-lg flex-col justify-center gap-4 px-4 py-12">
      <h1 className="text-2xl font-semibold">No tienes acceso</h1>
      <p className="text-neutral-700 dark:text-neutral-300">
        Entraste con Cloudflare Access, pero tu correo todavía no está dado de alta en trading_maxxing. Pide al admin
        que te dé de alta y vuelve a intentarlo.
      </p>
      {detail && <p className="text-sm text-neutral-500">Detalle: {detail}</p>}
      <div className="flex flex-wrap gap-3">
        <a
          href={LOGOUT_URL}
          className="inline-flex items-center justify-center rounded-md border border-neutral-300 px-4 py-2 text-sm font-semibold hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          Entrar con otro correo
        </a>
      </div>
    </main>
  );
}

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data, error, isLoading, mutate } = useSWR<Me>(ME_KEY, { revalidateOnFocus: false });

  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return <AccessDenied detail={error.status === 403 ? error.message : undefined} />;
  }
  if (error && !data) {
    return (
      <main className="mx-auto w-full max-w-xl p-8">
        <ErrorBox onRetry={() => void mutate()}>
          No se pudo verificar tu sesión. {apiErrorMessage(error)}
        </ErrorBox>
      </main>
    );
  }
  if (isLoading || !data) {
    return (
      <main className="mx-auto w-full max-w-xl p-8">
        <Loading>Verificando sesión…</Loading>
      </main>
    );
  }
  return (
    <MeContext.Provider value={{ me: data, isAdmin: data.role === "admin", refresh: mutate }}>{children}</MeContext.Provider>
  );
}
