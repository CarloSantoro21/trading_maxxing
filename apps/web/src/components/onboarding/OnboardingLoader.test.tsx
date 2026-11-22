import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { AppShell } from "@/components/nav/AppShell";
import { mockApi, type Routes } from "@/test/api";
import { renderWithApi } from "@/test/render";
import { OnboardingLoader } from "./OnboardingLoader";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/onboarding",
  useSearchParams: () => new URLSearchParams(),
}));

function renderWith(routes: Routes) {
  const api = mockApi(routes);
  renderWithApi(
    <RequireAuth>
      <AppShell>
        <OnboardingLoader />
      </AppShell>
    </RequireAuth>,
  );
  return api;
}

describe("OnboardingLoader", () => {
  it("prellena el wizard con el perfil guardado", async () => {
    renderWith({
      "GET /onboarding": {
        id: "p1",
        risk_level: 5,
        capital: 1234,
        max_drawdown: 0.4,
        goals: [{ target_return: 0.1, deadline: "2099-01-01" }],
      },
    });
    expect(await screen.findByLabelText("Capital inicial (USDT)")).toHaveValue(1234);
  });

  it("muestra el wizard vacío si no hay perfil", async () => {
    renderWith({ "GET /onboarding": { body: null } });
    expect(await screen.findByLabelText("Capital inicial (USDT)")).toHaveValue(null);
  });

  it("muestra un error si no se puede leer el perfil", async () => {
    renderWith({ "GET /onboarding": { status: 500, body: { detail: "down" } } });
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo leer tu perfil");
  });

  it("al guardar vuelve a leer /api/me y el perfil", async () => {
    const api = renderWith({
      "GET /onboarding": { body: null },
      "PUT /onboarding": { profileId: "p1", profile_id: "p1" },
    });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Capital inicial (USDT)"), "1000");
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    await user.type(screen.getByLabelText("Retorno objetivo (%) · meta 1"), "10");
    fireEvent.change(screen.getByLabelText("Fecha límite · meta 1"), { target: { value: "2099-12-31" } });
    const before = api.calls.filter((c) => c.path === "/me").length;
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    expect(await screen.findByRole("heading", { name: "¡Perfil guardado!" })).toBeInTheDocument();
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/me").length).toBeGreaterThan(before));
  });

  it("marca Mi perfil como la página actual en la navegación", async () => {
    renderWith({ "GET /onboarding": { body: null } });
    expect(await screen.findByRole("navigation", { name: "Principal" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mi perfil" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "Estrategias" })).toHaveAttribute("href", "/strategies");
  });
});
