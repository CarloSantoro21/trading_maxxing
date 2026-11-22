"use client";

import useSWR, { useSWRConfig } from "swr";
import { useMe } from "@/components/auth/RequireAuth";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { DASHBOARD_KEY } from "@/lib/dashboard/data";
import { fetchProfile, ONBOARDING_KEY, profileToForm, type ProfileRow } from "@/lib/onboarding/repository";
import { OnboardingWizard } from "./OnboardingWizard";

/**
 * Carga el perfil existente (`GET /api/onboarding`) y muestra el wizard prellenado.
 * Debe ir dentro de <RequireAuth>; la navegación y el cierre de sesión los pone <AppShell>.
 * Al guardar vuelve a leer `/api/me` (que trae el perfil) y el dashboard.
 */
export function OnboardingLoader() {
  const { refresh } = useMe();
  const { mutate } = useSWRConfig();
  const { data, error, isLoading, mutate: reload } = useSWR<ProfileRow | null>(ONBOARDING_KEY, fetchProfile, {
    revalidateOnFocus: false,
  });

  function handleSaved() {
    void refresh();
    void mutate(DASHBOARD_KEY);
    void reload();
  }

  return (
    <div className="mx-auto w-full max-w-3xl">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Configura tu perfil de inversión</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          Con esto se ajustan el tamaño de las posiciones, los límites de riesgo y el kill switch.
        </p>
      </header>

      {isLoading && <Loading>Cargando tu perfil…</Loading>}
      {error && !isLoading && (
        <ErrorBox onRetry={() => void reload()}>{error instanceof Error ? error.message : "No se pudo leer tu perfil."}</ErrorBox>
      )}
      {!isLoading && !error && data !== undefined && (
        <OnboardingWizard initial={data ? profileToForm(data) : undefined} onSaved={handleSaved} />
      )}
    </div>
  );
}
