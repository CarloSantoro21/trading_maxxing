"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { fetchProfile, profileToForm } from "@/lib/onboarding/repository";
import type { OnboardingFormState } from "@/lib/onboarding/schema";
import { OnboardingWizard } from "./OnboardingWizard";

/**
 * Carga el perfil existente (si lo hay) y muestra el wizard prellenado.
 * Debe ir dentro de <RequireAuth>, que garantiza que hay sesión; la navegación y el cierre
 * de sesión los pone <AppShell>.
 */
export function OnboardingLoader() {
  const { supabase, session } = useAuth();
  const userId = session?.user.id;
  const [initial, setInitial] = useState<OnboardingFormState | undefined>(undefined);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    if (!supabase || !userId) return;
    let active = true;
    fetchProfile(supabase, userId)
      .then((row) => {
        if (!active) return;
        setInitial(row ? profileToForm(row) : undefined);
        setState("ready");
      })
      .catch(() => {
        if (active) setState("error");
      });
    return () => {
      active = false;
    };
  }, [supabase, userId]);

  return (
    <div className="mx-auto w-full max-w-3xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Configura tu perfil de inversión</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          Con esto se ajustan el tamaño de las posiciones, los límites de riesgo y el kill switch.
        </p>
      </header>

      {state === "loading" && (
        <p role="status" className="text-neutral-500">
          Cargando tu perfil…
        </p>
      )}
      {state === "error" && (
        <p role="alert" className="text-red-700 dark:text-red-400">
          No se pudo leer tu perfil. Revisa que Supabase esté corriendo y recarga la página.
        </p>
      )}
      {state === "ready" && supabase && userId && (
        <OnboardingWizard supabase={supabase} userId={userId} initial={initial} />
      )}
    </div>
  );
}
