"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { LoginForm } from "@/components/auth/LoginForm";
import { safeNextPath } from "@/components/auth/RequireAuth";
import { cardClass } from "@/components/ui/styles";
import { fetchProfile } from "@/lib/onboarding/repository";

export function LoginContent() {
  const { supabase, session, loading, configError } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");

  // Si ya hay sesión, no tiene sentido mostrar el formulario.
  useEffect(() => {
    if (!loading && session) router.replace(safeNextPath(next));
  }, [loading, session, next, router]);

  async function handleSuccess(userId: string) {
    if (next) {
      router.replace(safeNextPath(next));
      return;
    }
    // Sin destino explícito: al onboarding si todavía no tiene perfil y, si ya lo tiene, al dashboard.
    let destination = "/onboarding";
    try {
      if (supabase && (await fetchProfile(supabase, userId))) destination = "/dashboard";
    } catch {
      // Si no se puede leer el perfil, el onboarding lo volverá a intentar.
    }
    router.replace(destination);
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-12">
      <h1 className="text-2xl font-semibold">Iniciar sesión</h1>
      <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
        Entra con tu cuenta de trading_maxxing (Binance Testnet, sin dinero real).
      </p>
      <div className={`${cardClass} mt-6`}>
        {configError ? (
          <p role="alert" className="text-red-700 dark:text-red-400">
            {configError}
          </p>
        ) : supabase ? (
          <LoginForm supabase={supabase} onSuccess={handleSuccess} />
        ) : (
          <p role="status" className="text-neutral-500">
            Cargando…
          </p>
        )}
      </div>
    </main>
  );
}

