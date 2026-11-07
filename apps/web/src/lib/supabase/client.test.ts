import { afterEach, describe, expect, it, vi } from "vitest";

const createBrowserClient = vi.fn((url: string, key: string) => ({ url, key }));
vi.mock("@supabase/ssr", () => ({ createBrowserClient }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  createBrowserClient.mockClear();
});

describe("getSupabaseClient", () => {
  it("lanza un error claro si faltan las variables de entorno", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    const mod = await import("./client");
    expect(mod.getSupabaseConfig()).toBeNull();
    expect(() => mod.getSupabaseClient()).toThrow(mod.MissingSupabaseConfigError);
  });

  it("crea un solo cliente de navegador con la URL y la llave anon", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "http://127.0.0.1:54321");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "anon-key");
    const mod = await import("./client");
    const first = mod.getSupabaseClient();
    expect(mod.getSupabaseClient()).toBe(first);
    expect(createBrowserClient).toHaveBeenCalledTimes(1);
    expect(createBrowserClient).toHaveBeenCalledWith("http://127.0.0.1:54321", "anon-key");
  });
});
