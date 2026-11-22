"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useMe } from "@/components/auth/RequireAuth";
import { Loading } from "@/components/ui/feedback";

/**
 * Página de inicio: Cloudflare Access ya autenticó a la persona antes de cargar el sitio,
 * así que solo se decide el destino: el onboarding si todavía no tiene perfil y, si ya lo
 * tiene, el dashboard. Va dentro de <RequireAuth>.
 */
export function HomeRedirect() {
  const { me } = useMe();
  const router = useRouter();
  const destination = me.profile ? "/dashboard" : "/onboarding";

  useEffect(() => {
    router.replace(destination);
  }, [router, destination]);

  return (
    <main className="mx-auto w-full max-w-xl p-8">
      <h1 className="text-3xl font-semibold">trading_maxxing</h1>
      <Loading>Abriendo {me.profile ? "tu dashboard" : "la configuración de tu perfil"}…</Loading>
    </main>
  );
}
