import { SignJWT, exportJWK, generateKeyPair } from "jose";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { accessToken, normalizeTeamDomain, resetJwksCache } from "../src/auth/access";
import { safeEqual } from "../src/auth/middleware";
import { ADMIN, MEMBER, SERVICE_TOKEN, call, json, seedBase } from "./helpers";

const TEAM = "equipo-prueba.cloudflareaccess.com";
const AUD = "aud-de-prueba";
const ACCESS_ENV = { AUTH_MODE: "access", ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD };

let privateKey: CryptoKey;
let jwks: { keys: unknown[] };

beforeAll(async () => {
  await seedBase();
  const pair = await generateKeyPair("RS256", { extractable: true });
  privateKey = pair.privateKey as CryptoKey;
  const jwk = await exportJWK(pair.publicKey);
  jwks = { keys: [{ ...jwk, kid: "k1", alg: "RS256", use: "sig" }] };
});

afterEach(() => {
  vi.restoreAllMocks();
  resetJwksCache();
});

function mockCerts() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url === `https://${TEAM}/cdn-cgi/access/certs`) {
      return new Response(JSON.stringify(jwks), { headers: { "Content-Type": "application/json" } });
    }
    return new Response("no encontrado", { status: 404 });
  });
}

async function token(claims: Record<string, unknown>, opts: { aud?: string; iss?: string; exp?: string | number } = {}) {
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "k1" })
    .setIssuedAt()
    .setIssuer(opts.iss ?? `https://${TEAM}`)
    .setAudience(opts.aud ?? AUD)
    .setExpirationTime(opts.exp ?? "5m")
    .sign(privateKey);
}

describe("health", () => {
  it("responde sin autenticación", async () => {
    const res = await call("/api/health", { env: ACCESS_ENV });
    expect(res.status).toBe(200);
    expect(await json(res)).toMatchObject({ service: "api", status: "ok" });
    expect(res.headers.get("X-Request-Id")).toBeTruthy();
  });

  it("404 con el formato de FastAPI", async () => {
    const res = await call("/no-existe");
    expect(res.status).toBe(404);
    expect(await json(res)).toEqual({ detail: "Not Found" });
  });
});

describe("AUTH_MODE=dev", () => {
  it("usa DEV_USER_EMAIL por omisión", async () => {
    const me = await json(await call("/api/me"));
    expect(me).toMatchObject({ id: ADMIN.id, email: ADMIN.email, role: "admin" });
  });

  it("X-Dev-User cambia el usuario", async () => {
    const me = await json(await call("/api/me", { as: MEMBER.email }));
    expect(me.role).toBe("member");
  });

  it("403 si el correo no está dado de alta", async () => {
    const res = await call("/api/me", { as: "nadie@test.dev" });
    expect(res.status).toBe(403);
    expect((await json(res)).detail).toContain("no está dado de alta");
  });

  it("se rechaza en production", async () => {
    const res = await call("/api/me", { env: { ENVIRONMENT: "production" } });
    expect(res.status).toBe(500);
  });

  it("401 sin correo configurado", async () => {
    const res = await call("/api/me", { env: { DEV_USER_EMAIL: undefined } });
    expect(res.status).toBe(401);
  });

  it("AUTH_MODE inválido es un error de configuración", async () => {
    const res = await call("/api/me", { env: { AUTH_MODE: "otro" } });
    expect(res.status).toBe(500);
  });
});

describe("AUTH_MODE=access", () => {
  it("acepta un JWT válido de Access y busca el usuario por correo", async () => {
    mockCerts();
    const jwt = await token({ email: MEMBER.email.toUpperCase() });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(200);
    expect((await json(res)).id).toBe(MEMBER.id);
  });

  it("acepta la cookie CF_Authorization", async () => {
    mockCerts();
    const jwt = await token({ email: ADMIN.email });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { Cookie: `otra=1; CF_Authorization=${jwt}` } });
    expect(res.status).toBe(200);
  });

  it("401 sin token", async () => {
    const res = await call("/api/me", { env: ACCESS_ENV });
    expect(res.status).toBe(401);
  });

  it("401 con AUD equivocado", async () => {
    mockCerts();
    const jwt = await token({ email: ADMIN.email }, { aud: "otra-app" });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });

  it("401 con emisor equivocado", async () => {
    mockCerts();
    const jwt = await token({ email: ADMIN.email }, { iss: "https://otro.cloudflareaccess.com" });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });

  it("401 con token expirado", async () => {
    mockCerts();
    const jwt = await token({ email: ADMIN.email }, { exp: Math.floor(Date.now() / 1000) - 60 });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });

  it("401 con firma de otra llave", async () => {
    mockCerts();
    const other = await generateKeyPair("RS256");
    const jwt = await new SignJWT({ email: ADMIN.email })
      .setProtectedHeader({ alg: "RS256", kid: "k1" })
      .setIssuer(`https://${TEAM}`)
      .setAudience(AUD)
      .setExpirationTime("5m")
      .sign(other.privateKey);
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });

  it("401 si el token no trae correo", async () => {
    mockCerts();
    const jwt = await token({ common_name: "abc.access" });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });

  it("403 si el correo validado no está en users", async () => {
    mockCerts();
    const jwt = await token({ email: "intruso@test.dev" });
    const res = await call("/api/me", { env: ACCESS_ENV, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(403);
  });

  it("401 si falta ACCESS_AUD", async () => {
    const jwt = await token({ email: ADMIN.email });
    const res = await call("/api/me", {
      env: { ...ACCESS_ENV, ACCESS_AUD: undefined },
      headers: { "Cf-Access-Jwt-Assertion": jwt },
    });
    expect(res.status).toBe(401);
  });
});

describe("/internal/*", () => {
  it("401 sin token", async () => {
    expect((await call("/internal/settings")).status).toBe(401);
  });

  it("401 con token incorrecto", async () => {
    const res = await call("/internal/settings", { headers: { "X-Service-Token": "malo" } });
    expect(res.status).toBe(401);
  });

  it("503 si el Worker no tiene SERVICE_TOKEN", async () => {
    const res = await call("/internal/settings", { headers: { "X-Service-Token": "x" }, env: { SERVICE_TOKEN: undefined } });
    expect(res.status).toBe(503);
  });

  it("200 con X-Service-Token", async () => {
    const res = await call("/internal/settings", { headers: { "X-Service-Token": SERVICE_TOKEN } });
    expect(res.status).toBe(200);
    expect(await json(res)).toEqual({ explainer_enabled: false, news_enabled: false });
  });

  it("acepta un service token de Access autorizado", async () => {
    mockCerts();
    const jwt = await token({ common_name: "motor.access" });
    const env = { ...ACCESS_ENV, ACCESS_SERVICE_CLIENT_IDS: "motor.access, otro" };
    const res = await call("/internal/settings", { env, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(200);
  });

  it("403 con un service token de Access no autorizado", async () => {
    mockCerts();
    const jwt = await token({ common_name: "intruso.access" });
    const env = { ...ACCESS_ENV, ACCESS_SERVICE_CLIENT_IDS: "motor.access" };
    const res = await call("/internal/settings", { env, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(403);
  });

  it("401 con un JWT de Access inválido", async () => {
    mockCerts();
    const jwt = await token({ common_name: "motor.access" }, { aud: "otra" });
    const env = { ...ACCESS_ENV, ACCESS_SERVICE_CLIENT_IDS: "motor.access" };
    const res = await call("/internal/settings", { env, headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(res.status).toBe(401);
  });
});

describe("utilidades de auth", () => {
  it("safeEqual compara en tiempo constante", () => {
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });

  it("normalizeTeamDomain quita esquema y diagonales", () => {
    expect(normalizeTeamDomain("https://x.cloudflareaccess.com/")).toBe("x.cloudflareaccess.com");
  });

  it("accessToken lee header o cookie", () => {
    expect(accessToken(new Headers({ "Cf-Access-Jwt-Assertion": "a" }))).toBe("a");
    expect(accessToken(new Headers({ Cookie: "CF_Authorization=b%2Bc" }))).toBe("b+c");
    expect(accessToken(new Headers())).toBeNull();
  });
});
