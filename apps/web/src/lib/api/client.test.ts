import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/api";
import {
  ApiError,
  apiErrorMessage,
  apiRequest,
  createBot,
  errorDetail,
  getApiUrl,
  killBot,
  MissingApiConfigError,
  startBot,
  stopBot,
  transitionStrategy,
} from "./client";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("manda el token y el cuerpo JSON", async () => {
    const { calls } = mockApi(() => ({ body: { ok: true } }));
    await expect(apiRequest("/x", { token: "jwt", method: "POST", body: { a: 1 } })).resolves.toEqual({ ok: true });
    expect(calls[0]).toMatchObject({ method: "POST", path: "/x", body: { a: 1 } });
    expect(calls[0].headers).toEqual({ Authorization: "Bearer jwt", "Content-Type": "application/json" });
  });

  it("sin cuerpo no manda Content-Type y acepta respuestas vacías o de texto", async () => {
    const { calls } = mockApi(() => ({ status: 200 }));
    await expect(apiRequest("/x", { token: "t" })).resolves.toBeNull();
    expect(calls[0].headers).toEqual({ Authorization: "Bearer t" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("hola", { status: 200 })));
    await expect(apiRequest("/x", { token: "t" })).resolves.toBe("hola");
  });

  it("convierte los errores de FastAPI en ApiError", async () => {
    mockApi(() => ({ status: 409, body: { detail: "No se puede arrancar" } }));
    const err = await apiRequest("/x", { token: "t" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("No se puede arrancar");
  });

  it("explica un error de red", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test/");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const err = await apiRequest("/x", { token: "t" }).catch((e) => e);
    expect(err.status).toBe(0);
    expect(err.message).toContain("http://api.test");
  });

  it("falla con un mensaje claro si falta NEXT_PUBLIC_API_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(getApiUrl()).toBeNull();
    await expect(apiRequest("/x", { token: "t" })).rejects.toBeInstanceOf(MissingApiConfigError);
  });
});

describe("errorDetail", () => {
  it.each([
    [{ detail: [{ msg: "campo requerido" }, { msg: "muy largo" }] }, 422, "campo requerido; muy largo"],
    [{ detail: [{}] }, 422, "La API respondió con un error (422)."],
    [null, 401, "Tu sesión expiró; vuelve a iniciar sesión."],
    ["x", 403, "No tienes permiso para hacer esto."],
    [{}, 500, "La API respondió con un error (500)."],
  ])("errorDetail(%j, %i)", (body, status, expected) => {
    expect(errorDetail(body, status)).toBe(expected);
  });
});

describe("endpoints", () => {
  it("usa las rutas de bots y estrategias", async () => {
    const { calls } = mockApi(() => ({ body: {} }));
    const opts = { token: "t" };
    await createBot("Mi bot", opts);
    await startBot("b 1", opts);
    await stopBot("b1", opts);
    await killBot("b1", "pánico", opts);
    await transitionStrategy("trend", "promote", "7 días", opts);
    await transitionStrategy("trend", "retire", "drawdown", opts);
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /bots",
      "POST /bots/b%201/start",
      "POST /bots/b1/stop",
      "POST /bots/b1/kill",
      "POST /strategies/trend/promote",
      "POST /strategies/trend/retire",
    ]);
    expect(calls.map((c) => c.body)).toEqual([
      { name: "Mi bot" },
      undefined,
      undefined,
      { reason: "pánico" },
      { reason: "7 días" },
      { reason: "drawdown" },
    ]);
  });
});

describe("apiErrorMessage", () => {
  it("usa el mensaje de los errores conocidos y uno genérico para lo demás", () => {
    expect(apiErrorMessage(new ApiError(409, "conflicto"))).toBe("conflicto");
    expect(apiErrorMessage(new MissingApiConfigError())).toContain("NEXT_PUBLIC_API_URL");
    expect(apiErrorMessage(new Error("x"))).toBe("Ocurrió un error inesperado. Intenta de nuevo.");
  });
});
