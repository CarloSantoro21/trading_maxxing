import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEMO_ME, mockApi } from "@/test/api";
import { renderWithApi } from "@/test/render";
import Home from "@/app/page";
import { LOGOUT_URL, RequireAuth, useMe } from "./RequireAuth";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/",
}));

beforeEach(() => replace.mockReset());
afterEach(() => vi.unstubAllGlobals());

function Private() {
  const { me, isAdmin } = useMe();
  return (
    <p>
      contenido de {me.email} {isAdmin ? "(admin)" : "(member)"}
    </p>
  );
}

describe("RequireAuth", () => {
  it("pide /api/me y muestra el contenido con el usuario", async () => {
    const { calls } = mockApi();
    renderWithApi(
      <RequireAuth>
        <Private />
      </RequireAuth>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Verificando sesión…");
    expect(await screen.findByText(/contenido de demo@trading-maxxing.local \(member\)/)).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ method: "GET", path: "/me" });
  });

  it("detecta al admin", async () => {
    mockApi({ "GET /me": { ...DEMO_ME, role: "admin" } });
    renderWithApi(
      <RequireAuth>
        <Private />
      </RequireAuth>,
    );
    expect(await screen.findByText(/\(admin\)/)).toBeInTheDocument();
  });

  it.each([401, 403])("con %i muestra que no tiene acceso y ofrece entrar con otro correo", async (status) => {
    mockApi({ "GET /me": { status, body: { detail: "El correo ana@iteso.mx no está dado de alta" } } });
    renderWithApi(
      <RequireAuth>
        <Private />
      </RequireAuth>,
    );
    expect(await screen.findByRole("heading", { name: "No tienes acceso" })).toBeInTheDocument();
    expect(screen.getByText(/Pide al admin que te dé de alta/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Entrar con otro correo" })).toHaveAttribute("href", LOGOUT_URL);
    expect(screen.queryByText(/contenido de/)).not.toBeInTheDocument();
    if (status === 403) expect(screen.getByText(/Detalle: El correo ana@iteso.mx/)).toBeInTheDocument();
  });

  it("si la API no responde muestra el error y reintenta", async () => {
    let fail = true;
    mockApi({ "GET /me": () => (fail ? { status: 500, body: { detail: "caída" } } : { body: DEMO_ME }) });
    renderWithApi(
      <RequireAuth>
        <Private />
      </RequireAuth>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("caída");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(/contenido de/)).toBeInTheDocument();
  });
});

describe("useMe", () => {
  it("falla fuera de RequireAuth", () => {
    function Orphan() {
      useMe();
      return null;
    }
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Orphan />)).toThrow(/RequireAuth/);
  });
});

describe("Home", () => {
  it("manda al onboarding si el usuario aún no tiene perfil", async () => {
    mockApi();
    renderWithApi(<Home />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding"));
    expect(screen.getByRole("heading", { name: "trading_maxxing" })).toBeInTheDocument();
  });

  it("manda al dashboard si el usuario ya tiene perfil", async () => {
    mockApi({ "GET /me": { ...DEMO_ME, profile: { id: "p1" } } });
    renderWithApi(<Home />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
  });
});
