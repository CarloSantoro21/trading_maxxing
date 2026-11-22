import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/api";
import { ApiError, apiBase, apiFetch, errorDetail, triggerDemoError } from "./client";

afterEach(() => vi.unstubAllEnvs());

describe("apiBase", () => {
  it("usa el mismo origen por defecto", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    expect(apiBase()).toBe("/api");
  });

  it("acepta otro origen sin la diagonal final", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8787/");
    expect(apiBase()).toBe("http://localhost:8787/api");
  });
});

describe("apiFetch", () => {
  it("llama a /api con la cookie de Access, sin tokens y con el body en JSON", async () => {
    const { fetchMock, calls } = mockApi({ "PUT /onboarding": { profileId: "p1", profile_id: "p1" } });
    const res = await apiFetch("/onboarding", { method: "PUT", body: { capital: 600 } });
    expect(res).toEqual({ profileId: "p1", profile_id: "p1" });
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe("/api/onboarding");
    expect(init).toMatchObject({ method: "PUT", credentials: "include", redirect: "manual" });
    expect(calls.at(-1)?.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
  });

  it("devuelve undefined con 204", async () => {
    mockApi({ "DELETE /admin/users/u2": { status: 204 } });
    await expect(apiFetch("/admin/users/u2", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("lanza ApiError con el detail de la API", async () => {
    mockApi({ "POST /bots": { status: 409, body: { detail: "Ya tienes un bot" } } });
    const err = await apiFetch("/bots", { method: "POST", body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, message: "Ya tienes un bot" });
  });

  it("une los mensajes de un 422 de validación", () => {
    expect(errorDetail({ detail: [{ msg: "capital >= 500" }, { msg: "riesgo 1..5" }] }, 422)).toBe("capital >= 500; riesgo 1..5");
    expect(errorDetail(null, 401)).toMatch(/Cloudflare Access/);
    expect(errorDetail("html", 502)).toBe("La API respondió con un error (502).");
  });

  it("sin conexión lanza ApiError con status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(apiFetch("/me")).rejects.toMatchObject({ status: 0 });
  });

  it("si Access redirige al login (sesión vencida) lo trata como 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ type: "opaqueredirect" } as Response));
    await expect(apiFetch("/me")).rejects.toMatchObject({ status: 401 });
  });
});

describe("triggerDemoError", () => {
  it("devuelve el request_id del 500 a propósito", async () => {
    mockApi({ "POST /admin/debug/error": { status: 500, body: { detail: "x", request_id: "r-1" } } });
    await expect(triggerDemoError()).resolves.toBe("r-1");
  });
});
