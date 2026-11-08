import type { SupabaseClient } from "@supabase/supabase-js";
import { vi } from "vitest";

/**
 * Cliente de Supabase simulado para las pruebas (sin red).
 *
 * `from(tabla)` devuelve un query builder encadenable que registra cada llamada; al
 * hacer `await`, responde con lo que devuelva `respond(tabla, operación, llamadas)`.
 *
 * `channel(nombre)` imita Supabase Realtime: registra los `on("postgres_changes", ...)` y
 * `emitRealtime(tabla, evento, fila)` los dispara como lo haría el servidor.
 */

export type Op = "select" | "insert" | "upsert" | "update" | "delete";

export interface QueryCall {
  table: string;
  op: Op;
  calls: { method: string; args: unknown[] }[];
}

export type Responder = (query: QueryCall) => { data?: unknown; error?: unknown };

const BUILDER_METHODS = [
  "select",
  "insert",
  "upsert",
  "update",
  "delete",
  "eq",
  "neq",
  "in",
  "is",
  "not",
  "gte",
  "lte",
  "order",
  "limit",
  "single",
  "maybeSingle",
];

interface RealtimeListener {
  channel: string;
  table: string;
  event: string;
  callback: (payload: unknown) => void;
}

/** ¿La consulta llamó a `method` (con `firstArg` como primer argumento, si se da)? */
export function called(query: QueryCall, method: string, firstArg?: unknown): boolean {
  return query.calls.some((c) => c.method === method && (firstArg === undefined || c.args[0] === firstArg));
}

export function createFakeSupabase(respond: Responder = () => ({ data: null, error: null })) {
  const queries: QueryCall[] = [];

  function from(table: string) {
    const query: QueryCall = { table, op: "select", calls: [] };
    queries.push(query);
    const builder: Record<string, unknown> = {};
    for (const method of BUILDER_METHODS) {
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

  const listeners: RealtimeListener[] = [];
  /** Estado que reporta `subscribe` (p. ej. "CHANNEL_ERROR" para simular una falla). */
  let subscribeStatus = "SUBSCRIBED";

  function channel(name: string) {
    const ch: Record<string, unknown> = { name };
    ch.on = (_type: string, filter: { table: string; event: string }, callback: (p: unknown) => void) => {
      listeners.push({ channel: name, table: filter.table, event: filter.event, callback });
      return ch;
    };
    ch.subscribe = (cb?: (status: string) => void) => {
      cb?.(subscribeStatus);
      return ch;
    };
    return ch;
  }

  const removeChannel = vi.fn(async (ch: { name: string }) => {
    for (let i = listeners.length - 1; i >= 0; i--) {
      if (listeners[i].channel === ch.name) listeners.splice(i, 1);
    }
    return "ok";
  });

  const client = {
    from: vi.fn(from),
    auth,
    channel: vi.fn(channel),
    removeChannel,
  } as unknown as SupabaseClient;

  return {
    client,
    auth,
    queries,
    unsubscribe,
    removeChannel,
    /** Simula un evento de Supabase Auth (p. ej. SIGNED_IN). */
    emitAuth(event: string, session: unknown) {
      authListener?.(event, session);
    },
    /** Simula un cambio de Postgres que llega por Realtime. */
    emitRealtime(table: string, eventType: "INSERT" | "UPDATE" | "DELETE", row: unknown, old: unknown = {}) {
      for (const l of [...listeners]) {
        if (l.table === table && (l.event === "*" || l.event === eventType)) {
          l.callback({ schema: "public", table, eventType, new: row, old });
        }
      }
    },
    /** Número de suscripciones activas a una tabla. */
    listenerCount(table: string) {
      return listeners.filter((l) => l.table === table).length;
    },
    setSubscribeStatus(status: string) {
      subscribeStatus = status;
    },
  };
}

export type FakeSupabase = ReturnType<typeof createFakeSupabase>;
