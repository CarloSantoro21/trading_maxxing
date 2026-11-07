import type { SupabaseClient } from "@supabase/supabase-js";
import { vi } from "vitest";

/**
 * Cliente de Supabase simulado para las pruebas (sin red).
 *
 * `from(tabla)` devuelve un query builder encadenable que registra cada llamada; al
 * hacer `await`, responde con lo que devuelva `respond(tabla, operación, llamadas)`.
 */

export type Op = "select" | "insert" | "upsert" | "update" | "delete";

export interface QueryCall {
  table: string;
  op: Op;
  calls: { method: string; args: unknown[] }[];
}

export type Responder = (query: QueryCall) => { data?: unknown; error?: unknown };

export function createFakeSupabase(respond: Responder = () => ({ data: null, error: null })) {
  const queries: QueryCall[] = [];

  function from(table: string) {
    const query: QueryCall = { table, op: "select", calls: [] };
    queries.push(query);
    const builder: Record<string, unknown> = {};
    for (const method of ["select", "insert", "upsert", "update", "delete", "eq", "single", "maybeSingle"]) {
      builder[method] = (...args: unknown[]) => {
        query.calls.push({ method, args });
        if (method === "insert" || method === "upsert" || method === "update" || method === "delete") {
          query.op = method;
        }
        return builder;
      };
    }
    builder.then = (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => {
      try {
        const { data = null, error = null } = respond(query);
        return Promise.resolve({ data, error }).then(resolve, reject);
      } catch (err) {
        return Promise.reject(err).then(resolve, reject);
      }
    };
    return builder;
  }

  const unsubscribe = vi.fn();
  let authListener: ((event: string, session: unknown) => void) | null = null;

  const auth = {
    signInWithPassword: vi.fn(async () => ({
      data: { user: { id: "user-1" }, session: {} },
      error: null as { message: string } | null,
    })),
    getSession: vi.fn(async () => ({ data: { session: null as unknown }, error: null })),
    onAuthStateChange: vi.fn((cb: (event: string, session: unknown) => void) => {
      authListener = cb;
      return { data: { subscription: { unsubscribe } } };
    }),
    signOut: vi.fn(async () => ({ error: null })),
  };

  const client = { from: vi.fn(from), auth } as unknown as SupabaseClient;

  return {
    client,
    auth,
    queries,
    unsubscribe,
    /** Simula un evento de Supabase Auth (p. ej. SIGNED_IN). */
    emitAuth(event: string, session: unknown) {
      authListener?.(event, session);
    },
  };
}
