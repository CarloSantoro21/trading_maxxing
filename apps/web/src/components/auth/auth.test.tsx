import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeSupabase } from "@/test/fakeSupabase";
import { AuthProvider, useAuth } from "./AuthProvider";
import { LoginContent } from "./LoginContent";
import { RequireAuth, safeNextPath } from "./RequireAuth";

const replace = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/onboarding",
  useSearchParams: () => searchParams,
}));

const session = { user: { id: "user-1" } };

beforeEach(() => {
  replace.mockReset();
  searchParams = new URLSearchParams();
});

describe("RequireAuth", () => {
  it("redirige a /login con next si no hay sesión", async () => {
    const fake = createFakeSupabase();
    render(
      <AuthProvider client={fake.client}>
        <RequireAuth>
          <p>contenido privado</p>
        </RequireAuth>
      </AuthProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Verificando sesión…");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?next=%2Fonboarding"));
    expect(screen.queryByText("contenido privado")).not.toBeInTheDocument();
  });

  it("muestra el contenido si hay sesión", async () => {
    const fake = createFakeSupabase();
    fake.auth.getSession.mockResolvedValueOnce({ data: { session }, error: null });
    render(
      <AuthProvider client={fake.client}>
        <RequireAuth>
          <p>contenido privado</p>
        </RequireAuth>
      </AuthProvider>,
    );
    expect(await screen.findByText("contenido privado")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("reacciona a los cambios de sesión y se desuscribe al desmontar", async () => {
    const fake = createFakeSupabase();
    const { unmount } = render(
      <AuthProvider client={fake.client}>
        <RequireAuth>
          <p>contenido privado</p>
        </RequireAuth>
      </AuthProvider>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalled());
    act(() => fake.emitAuth("SIGNED_IN", session));
    expect(await screen.findByText("contenido privado")).toBeInTheDocument();
    unmount();
    expect(fake.unsubscribe).toHaveBeenCalled();
  });

  it("muestra el error de configuración si faltan las variables de entorno", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    render(
      <AuthProvider>
        <RequireAuth>
          <p>contenido privado</p>
        </RequireAuth>
      </AuthProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("NEXT_PUBLIC_SUPABASE_URL");
    expect(replace).not.toHaveBeenCalled();
    vi.unstubAllEnvs();
  });
});

describe("useAuth", () => {
  it("falla fuera de AuthProvider", () => {
    function Orphan() {
      useAuth();
      return null;
    }
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/AuthProvider/);
  });
});

describe("safeNextPath", () => {
  it.each([
    [null, "/onboarding"],
    ["/dashboard", "/dashboard"],
    ["https://malo.com", "/onboarding"],
    ["//malo.com", "/onboarding"],
    ["/\\malo.com", "/onboarding"],
  ])("safeNextPath(%j) = %j", (next, expected) => {
    expect(safeNextPath(next)).toBe(expected);
  });
});

describe("LoginContent", () => {
  async function login(fake: ReturnType<typeof createFakeSupabase>) {
    const user = userEvent.setup();
    render(
      <AuthProvider client={fake.client}>
        <LoginContent />
      </AuthProvider>,
    );
    await user.type(await screen.findByLabelText("Correo electrónico"), "demo@trading-maxxing.local");
    await user.type(screen.getByLabelText("Contraseña"), "demo1234");
    await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
  }

  it("manda al onboarding si el usuario aún no tiene perfil", async () => {
    const fake = createFakeSupabase(() => ({ data: null }));
    await login(fake);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding"));
  });

  it("manda al inicio si el usuario ya tiene perfil", async () => {
    const fake = createFakeSupabase(() => ({ data: { id: "p1" } }));
    await login(fake);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("respeta el parámetro next", async () => {
    searchParams = new URLSearchParams("next=/dashboard");
    const fake = createFakeSupabase();
    await login(fake);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(fake.queries).toHaveLength(0);
  });

  it("si ya hay sesión redirige sin mostrar el formulario de nuevo", async () => {
    const fake = createFakeSupabase();
    fake.auth.getSession.mockResolvedValueOnce({ data: { session }, error: null });
    render(
      <AuthProvider client={fake.client}>
        <LoginContent />
      </AuthProvider>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding"));
  });
});
