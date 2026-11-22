import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { DEMO_ME, ENGINE_ONLINE, mockApi, type Routes } from "@/test/api";
import { renderWithApi } from "@/test/render";
import { ADMIN_LINK, AppShell, NAV_LINKS } from "./AppShell";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/strategies",
}));

afterEach(() => vi.unstubAllEnvs());

function renderShell(routes: Routes = {}) {
  mockApi(routes);
  renderWithApi(
    <RequireAuth>
      <AppShell>
        <p>contenido</p>
      </AppShell>
    </RequireAuth>,
  );
}

function navHrefs() {
  const nav = screen.getByRole("navigation", { name: "Principal" });
  return within(nav)
    .getAllByRole("link")
    .map((a) => a.getAttribute("href"));
}

describe("AppShell", () => {
  it("navega entre las páginas y cierra sesión con el logout de Access", async () => {
    renderShell({ "GET /me": { ...DEMO_ME, email: "ana@iteso.mx" } });
    expect(await screen.findByText("ana@iteso.mx")).toBeInTheDocument();
    expect(navHrefs()).toEqual(NAV_LINKS.map((l) => l.href));
    expect(screen.getByRole("link", { name: "Estrategias" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Cerrar sesión" })).toHaveAttribute("href", "/cdn-cgi/access/logout");
    expect(screen.getByText("contenido")).toBeInTheDocument();
  });

  it("al admin le muestra la liga al panel de admin", async () => {
    renderShell({ "GET /me": { ...DEMO_ME, role: "admin" } });
    await screen.findByText("contenido");
    expect(navHrefs()).toEqual([...NAV_LINKS, ADMIN_LINK].map((l) => l.href));
  });

  it("indica si el motor está en línea", async () => {
    renderShell();
    expect(await screen.findByText("Motor en línea")).toBeInTheDocument();
  });

  it("avisa si el engine ejecuta simulado (sin llaves de Testnet)", async () => {
    const engine = {
      component: "engine",
      status: "ok",
      version: null,
      host: null,
      started_at: null,
      last_seen_at: "2026-11-21T10:00:00.000Z",
      age_seconds: 5,
      online: true,
      details: { mode: "sim" },
    };
    renderShell({ "GET /engine/status": { ...ENGINE_ONLINE, components: [engine] } });
    expect(await screen.findByText("Motor en línea (simulado)")).toBeInTheDocument();
  });

  it("indica si el motor está desconectado", async () => {
    renderShell({ "GET /engine/status": { ...ENGINE_ONLINE, online: false } });
    expect(await screen.findByText("Motor desconectado")).toBeInTheDocument();
  });

  it("si la API del motor falla lo marca como desconectado", async () => {
    renderShell({ "GET /engine/status": { status: 500, body: { detail: "caída" } } });
    expect(await screen.findByText("Motor desconectado")).toBeInTheDocument();
  });

  it("muestra el ambiente y el commit del build", async () => {
    vi.stubEnv("NEXT_PUBLIC_ENV", "dev");
    vi.stubEnv("NEXT_PUBLIC_COMMIT_SHA", "abc1234def5678");
    renderShell();
    expect(await screen.findByTestId("env-badge")).toHaveTextContent("dev·abc1234");
  });

  it("sin variables el badge dice local", async () => {
    vi.stubEnv("NEXT_PUBLIC_ENV", "");
    vi.stubEnv("NEXT_PUBLIC_COMMIT_SHA", "");
    renderShell();
    expect(await screen.findByTestId("env-badge")).toHaveTextContent("local·sin versión");
  });
});
