import type { Session } from "@supabase/supabase-js";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { createFakeSupabase } from "@/test/fakeSupabase";
import { AppShell, NAV_LINKS } from "./AppShell";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/strategies",
}));

describe("AppShell", () => {
  it("navega entre dashboard, estrategias y perfil, y cierra sesión", async () => {
    const fake = createFakeSupabase();
    fake.auth.getSession.mockResolvedValue({
      data: { session: { user: { id: "u1", email: "ana@iteso.mx" } } as unknown as Session },
      error: null,
    });
    render(
      <AuthProvider client={fake.client}>
        <AppShell>
          <p>contenido</p>
        </AppShell>
      </AuthProvider>,
    );
    const nav = screen.getByRole("navigation", { name: "Principal" });
    const links = Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toEqual(NAV_LINKS.map((l) => l.href));
    expect(screen.getByRole("link", { name: "Estrategias" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(await screen.findByText("ana@iteso.mx")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(fake.auth.signOut).toHaveBeenCalled();
  });
});
