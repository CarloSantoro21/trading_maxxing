import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { fakeFetcher, sessionClient } from "@/test/fakeApi";
import { PROJECTION } from "@/test/fixtures";
import { GoalsView } from "./GoalsView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/goals",
}));

const GOALS = [
  { id: "g1", profile_id: "p1", target_return: 0.2, deadline: "2027-06-30" },
  { id: "g2", profile_id: "p1", target_return: 0.5, deadline: "2028-01-01" },
];

function renderView(fetcher: ReturnType<typeof fakeFetcher>) {
  render(
    <AuthProvider client={sessionClient()}>
      <GoalsView fetcher={fetcher} />
    </AuthProvider>,
  );
}

describe("GoalsView", () => {
  it("muestra la probabilidad, el abanico y las estrategias de la primera meta", async () => {
    const fetcher = fakeFetcher({
      "GET /goals": GOALS,
      "GET /goals/g1/probability": PROJECTION,
      "GET /goals/g2/probability": { ...PROJECTION, goal_id: "g2", probability: 0.85 },
    });
    renderView(fetcher);
    expect(await screen.findByText("35 %")).toBeInTheDocument();
    expect(screen.getByText("Poco probable")).toBeInTheDocument();
    expect(screen.getByText(/2,000 trayectorias/)).toBeInTheDocument();
    expect(screen.getByText("Portafolio de referencia")).toBeInTheDocument();
    expect(screen.getByText("Sin historial suficiente para kronos.")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Meta"), "g2");
    expect(await screen.findByText("85 %")).toBeInTheDocument();
    expect(screen.getByText("Muy probable")).toBeInTheDocument();
  });

  it("avisa si no hay metas", async () => {
    renderView(fakeFetcher({ "GET /goals": [] }));
    expect(await screen.findByText(/No tienes metas/)).toBeInTheDocument();
  });

  it("muestra el error de la proyección", async () => {
    renderView(
      fakeFetcher({
        "GET /goals": [GOALS[0]],
        "GET /goals/g1/probability": { status: 409, body: { detail: "Sin capital" } },
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Sin capital");
    expect(screen.queryByLabelText("Meta")).not.toBeInTheDocument();
  });

  it("muestra el error de la lista de metas", async () => {
    renderView(fakeFetcher({ "GET /goals": { status: 500, body: { detail: "caída" } } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("caída");
  });
});
