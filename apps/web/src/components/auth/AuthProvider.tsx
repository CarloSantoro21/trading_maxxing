"use client";

import type { Session, SupabaseClient } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getSupabaseClient } from "@/lib/supabase/client";

/**
 * Sesión de Supabase Auth disponible para toda la app.
 *
 * Todo ocurre en el navegador (el sitio se publica como estático), por lo que la
 * sesión se resuelve en un efecto y mientras tanto `loading` es true.
 */

interface AuthContextValue {
  supabase: SupabaseClient | null;
  session: Session | null;
  loading: boolean;
  /** Mensaje si el cliente no se pudo crear (p. ej. faltan variables de entorno). */
  configError: string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({
  children,
  client,
}: {
  children: React.ReactNode;
  /** Cliente inyectable para pruebas; por defecto se crea con las variables de entorno. */
  client?: SupabaseClient;
}) {
  const [supabase, setSupabase] = useState<SupabaseClient | null>(client ?? null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    let sb: SupabaseClient;
    try {
      sb = client ?? getSupabaseClient();
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : String(err));
      setLoading(false);
      return;
    }
    setSupabase(sb);

    let active = true;
    sb.auth
      .getSession()
      .then(({ data }) => {
        if (active) setSession(data.session);
      })
      .catch(() => {
        if (active) setSession(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    const { data: subscription } = sb.auth.onAuthStateChange((_event, next) => {
      if (!active) return;
      setSession(next);
      setLoading(false);
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, [client]);

  const value = useMemo(
    () => ({ supabase, session, loading, configError }),
    [supabase, session, loading, configError],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>.");
  return ctx;
}
