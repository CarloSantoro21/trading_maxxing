import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { DEMO_ME, mockApi, type ApiCall, type Routes } from "@/test/api";
import { renderWithApi } from "@/test/render";
import { AdminView } from "./AdminView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin",
}));

const ADMIN = { ...DEMO_ME, role: "admin" as const };
const SELF = { id: ADMIN.id, email: ADMIN.email, role: "admin", created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z" };
const ANA = { id: "user-2", email: "ana@iteso.mx", role: "member", created_at: "2026-11-02T00:00:00Z", updated_at: "2026-11-02T00:00:00Z" };

function renderAdmin(routes: Routes) {
  const api = mockApi({ "GET /me": ADMIN, "GET /admin/settings": { news_enabled: false, explainer_enabled: true }, ...routes });
  renderWithApi(
    <RequireAuth>
      <AdminView />
    </RequireAuth>,
  );
  return api;
}

describe("AdminView", () => {
  it("a un member no le muestra el panel", async () => {
    mockApi();
    renderWithApi(
      <RequireAuth>
        <AdminView />
      </RequireAuth>,
    );
    expect(await screen.findByText("Solo para admins")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("lista usuarios y no deja que el admin se quite el rol ni se dé de baja", async () => {
    renderAdmin({ "GET /admin/users": [SELF, ANA] });
    const table = await screen.findByRole("table", { name: "Usuarios dados de alta" });
    expect(within(table).getByText("(tú)")).toBeInTheDocument();
    expect(screen.getByLabelText(`Rol de ${ADMIN.email}`)).toBeDisabled();
    expect(screen.getByRole("button", { name: `Dar de baja a ${ADMIN.email}` })).toBeDisabled();
    expect(screen.getByLabelText("Rol de ana@iteso.mx")).toHaveValue("member");
  });

  it("da de alta a un usuario y recarga la lista", async () => {
    let users = [SELF];
    const api = renderAdmin({
      "GET /admin/users": () => ({ body: users }),
      "POST /admin/users": (call: ApiCall) => {
        const body = call.body as { email: string; role: string };
        const created = { ...ANA, email: body.email, role: body.role };
        users = [SELF, created];
        return { status: 201, body: created };
      },
    });
    await screen.findByRole("table");
    await userEvent.type(screen.getByLabelText("Correo"), " ana@iteso.mx ");
    await userEvent.selectOptions(screen.getByLabelText("Rol"), "admin");
    await userEvent.click(screen.getByRole("button", { name: "Dar de alta" }));
    expect(await screen.findByText("ana@iteso.mx quedó dado de alta como admin.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")).toMatchObject({
      path: "/admin/users",
      body: { email: "ana@iteso.mx", role: "admin" },
    });
    expect(await screen.findByLabelText("Rol de ana@iteso.mx")).toHaveValue("admin");
  });

  it("muestra el 409 si el correo ya existe y valida el correo vacío", async () => {
    renderAdmin({
      "GET /admin/users": [SELF],
      "POST /admin/users": { status: 409, body: { detail: "El correo ya está dado de alta" } },
    });
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Dar de alta" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Escribe el correo.");
    await userEvent.type(screen.getByLabelText("Correo"), "ana@iteso.mx");
    await userEvent.click(screen.getByRole("button", { name: "Dar de alta" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("El correo ya está dado de alta"));
  });

  it("cambia el rol de un usuario", async () => {
    const api = renderAdmin({
      "GET /admin/users": [SELF, ANA],
      "PATCH /admin/users/user-2": { ...ANA, role: "admin" },
    });
    await userEvent.selectOptions(await screen.findByLabelText("Rol de ana@iteso.mx"), "admin");
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH")).toMatchObject({ path: "/admin/users/user-2", body: { role: "admin" } }),
    );
  });

  it("da de baja con confirmación", async () => {
    const api = renderAdmin({
      "GET /admin/users": [SELF, ANA],
      "DELETE /admin/users/user-2": { status: 204 },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Dar de baja a ana@iteso.mx" }));
    expect(screen.getByText("¿Dar de baja a ana@iteso.mx?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sí, dar de baja" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/admin/users/user-2")).toBe(true));
  });

  it("prende y apaga los interruptores de noticias y explicaciones", async () => {
    const api = renderAdmin({
      "GET /admin/users": [SELF],
      "PUT /admin/settings": (call: ApiCall) => ({
        body: { news_enabled: false, explainer_enabled: true, ...(call.body as object) },
      }),
    });
    const news = await screen.findByRole("switch", { name: "Noticias" });
    const explainer = screen.getByRole("switch", { name: "Explicaciones con LLM" });
    expect(news).toHaveAttribute("aria-checked", "false");
    expect(explainer).toHaveAttribute("aria-checked", "true");
    await userEvent.click(news);
    await waitFor(() => expect(screen.getByRole("switch", { name: "Noticias" })).toHaveAttribute("aria-checked", "true"));
    expect(api.calls.find((c) => c.method === "PUT")).toMatchObject({ path: "/admin/settings", body: { news_enabled: true } });
  });

  it("muestra el error al guardar un ajuste", async () => {
    renderAdmin({
      "GET /admin/users": [SELF],
      "PUT /admin/settings": { status: 422, body: { detail: "llave desconocida" } },
    });
    await userEvent.click(await screen.findByRole("switch", { name: "Noticias" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("llave desconocida");
  });

  it("la demo de APM muestra el request_id del 500", async () => {
    renderAdmin({
      "GET /admin/users": [SELF],
      "POST /admin/debug/error": { status: 500, body: { detail: "DemoError", request_id: "req-123" } },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Provocar error de prueba" }));
    expect(await screen.findByText(/request_id req-123/)).toBeInTheDocument();
  });
});
