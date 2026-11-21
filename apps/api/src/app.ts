/**
 * API de trading_maxxing en Cloudflare Workers (Hono + D1). Reemplaza a services/api
 * (FastAPI) y a Supabase (Postgres, Auth, PostgREST, RLS y Realtime). Issue #115.
 *
 * - /api/*       API pública para el frontend. Login con Cloudflare Access (o AUTH_MODE=dev).
 * - /internal/*  API para el motor de la Mac (X-Service-Token).
 * - /api/health  sin autenticación.
 *
 * Cada petición deja un log JSON con request_id, usuario, ruta, status y milisegundos, y
 * los errores no controlados se loguean con su stack (Workers Observability).
 */

import { Hono } from "hono";
import { cors } from "hono/cors";
import { requireService, requireUser } from "./auth/middleware";
import { ApiError, fromDbError } from "./lib/errors";
import { errorFields, log } from "./lib/log";
import { appData } from "./routes/app-data";
import { backtests } from "./routes/backtests";
import { bots } from "./routes/bots";
import { goals } from "./routes/goals";
import { internal } from "./routes/internal";
import { portfolio, trades } from "./routes/portfolio";
import { profiles } from "./routes/profiles";
import { strategies } from "./routes/strategies";
import { admin, users } from "./routes/users";
import type { AppEnv } from "./types";

export const VERSION = "0.2.0";

export function createApp(): Hono<AppEnv> {
  const app = new Hono<AppEnv>();

  // Request id + log estructurado de cada petición.
  app.use("*", async (c, next) => {
    const requestId = c.req.header("Cf-Ray") ?? crypto.randomUUID();
    c.set("requestId", requestId);
    const started = Date.now();
    try {
      await next();
    } finally {
      c.header("X-Request-Id", requestId);
      const status = c.res.status;
      log(status >= 500 ? "error" : status >= 400 ? "warn" : "info", "request", {
        request_id: requestId,
        method: c.req.method,
        path: c.req.path,
        route: c.req.routePath,
        status,
        ms: Date.now() - started,
        user: c.get("user")?.email ?? null,
        service: c.get("service") ?? null,
      });
    }
  });

  // CORS: el frontend (Pages) llama a la API desde el navegador con la cookie de Access.
  app.use(
    "/api/*",
    cors({
      origin: (origin, c) => {
        const allowed = (c.env.CORS_ORIGINS ?? "")
          .split(",")
          .map((o: string) => o.trim().replace(/\/+$/, ""))
          .filter(Boolean);
        return allowed.includes(origin) ? origin : null;
      },
      allowMethods: ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
      allowHeaders: ["Content-Type", "Authorization", "X-Dev-User"],
      credentials: true,
      maxAge: 600,
    }),
  );

  app.get("/api/health", (c) =>
    c.json({ service: "api", status: "ok", version: VERSION, environment: c.env.ENVIRONMENT ?? "dev", time: new Date().toISOString() }),
  );

  const api = new Hono<AppEnv>();
  api.use("*", requireUser);
  api.route("/", users);
  api.route("/admin", admin);
  api.route("/profiles", profiles);
  api.route("/goals", goals);
  api.route("/bots", bots);
  api.route("/portfolio", portfolio);
  api.route("/trades", trades);
  api.route("/backtests", backtests);
  api.route("/strategies", strategies);
  api.route("/", appData);
  app.route("/api", api);

  const svc = new Hono<AppEnv>();
  svc.use("*", requireService);
  svc.route("/", internal);
  app.route("/internal", svc);

  app.notFound((c) => c.json({ detail: "Not Found" }, 404));

  app.onError((err, c) => {
    const requestId = c.get("requestId");
    const apiError = err instanceof ApiError ? err : fromDbError(err);
    if (apiError) {
      if (apiError.status >= 500) log("error", "error de configuración", { request_id: requestId, ...errorFields(err) });
      return c.json(apiError.body(), apiError.status);
    }
    log("error", "excepción no controlada", {
      request_id: requestId,
      method: c.req.method,
      path: c.req.path,
      user: c.get("user")?.email ?? null,
      ...errorFields(err),
    });
    return c.json({ detail: "Error interno del servidor", request_id: requestId }, 500);
  });

  return app;
}
