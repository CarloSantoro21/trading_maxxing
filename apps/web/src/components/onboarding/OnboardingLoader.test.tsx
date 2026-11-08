import type { Session } from "@supabase/supabase-js";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { createFakeSupabase, type Responder } from "@/test/fakeSupabase";
import { OnboardingLoader } from "./OnboardingLoader";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/onboarding",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => replace.mockReset());

function renderWith(respond: Responder) {
  const fake = createFakeSupabase(respond);
  fake.auth.getSession.mockResolvedValue({
    data: { session: { user: { id: "user-1" } } as unknown as Session },
    error: null,
  });
  render(
    <AuthProvider client={fake.client}>
      <OnboardingLoader />
    </AuthProvider>,
  );
  return fake;
}

describe("OnboardingLoader", () => {
  it("prellena el wizard con el perfil guardado", async () => {
    renderWith(() => ({
      data: {
        id: "p1",
        risk_level: 5,
        capital: "1234",
        max_drawdown: "0.4",
        goals: [],
      },
    }));
    expect(await screen.findByLabelText("Capital inicial (USDT)")).toHaveValue(1234);
  });

  it("muestra el wizard vacío si no hay perfil", async () => {
    renderWith(() => ({ data: null }));
    expect(await screen.findByLabelText("Capital inicial (USDT)")).toHaveValue(null);
  });

  it("muestra un error si no se puede leer el perfil", async () => {
    renderWith(() => ({ error: { message: "down" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo leer tu perfil");
  });

  it("cierra sesión y vuelve a /login", async () => {
    const fake = renderWith(() => ({ data: null }));
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(fake.auth.signOut).toHaveBeenCalled();
  });
});
