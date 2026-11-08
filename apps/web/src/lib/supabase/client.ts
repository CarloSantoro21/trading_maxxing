import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

/**
 * Cliente de Supabase para el navegador.
 *
 * El frontend se publica como sitio estático (Cloudflare Pages), así que toda la
 * autenticación vive en el cliente: la sesión se guarda en cookies del navegador y
 * las políticas RLS de la base hacen el control de acceso real.
 *
 * El cliente se crea de forma perezosa (en efectos o manejadores de eventos) para que
 * el build estático no falle si las variables de entorno no están definidas.
 */

let client: SupabaseClient | null = null;

export class MissingSupabaseConfigError extends Error {
  constructor() {
    super(
      "Faltan NEXT_PUBLIC_SUPABASE_URL o NEXT_PUBLIC_SUPABASE_ANON_KEY. " +
        "Cópialas de `supabase status` a apps/web/.env.local.",
    );
    this.name = "MissingSupabaseConfigError";
  }
}

export function getSupabaseConfig(): { url: string; anonKey: string } | null {
  // Next.js reemplaza estas referencias en tiempo de build; deben escribirse literales.
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return null;
  return { url, anonKey };
}

export function getSupabaseClient(): SupabaseClient {
  if (client) return client;
  const config = getSupabaseConfig();
  if (!config) throw new MissingSupabaseConfigError();
  client = createBrowserClient(config.url, config.anonKey);
  return client;
}
