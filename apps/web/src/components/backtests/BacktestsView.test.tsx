import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { fakeFetcher, sessionClient } from "@/test/fakeApi";
import { DETAIL, SUMMARY } from "@/test/fixtures";
import { BacktestsView } from "./BacktestsView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/backtests",
}));

beforeEach(() => window.history.replaceState(null, "", "/backtests"));

function renderView(fetcher: ReturnType<typeof fakeFetcher>) {
  render(
    <AuthProvider client={sessionClient()}>
      <BacktestsView fetcher={fetcher} />
    </AuthProvider>,
  );
}

const JOB = {
  id: "12345678-aaaa",
  profile_id: "p1",
  status: "failed",
  config: { symbols: ["BTCUSDT"], start: "2021-01-01", end: "2022-01-01" },
  report: null,
  error: "sin datos",
  started_at: null,
  finished_at: null,
  created_at: "2026-11-08",
};

describe("BacktestsView", () => {
  it("lista los reportes y abre el detalle con veredicto, ventanas y Monte Carlo", async () => {
    const fetcher = fakeFetcher({
      "GET /lab/reports": [SUMMARY],
      "GET /lab/reports/trend": DETAIL,
      "GET /backtests": [JOB],
    });
    renderView(fetcher);
    const table = await screen.findByRole("table", { name: "Reportes del laboratorio de walk-forward" });
    expect(within(table).getByText("42.0 %")).toBeInTheDocument();
    expect(await screen.findByText(/Falló: sin datos/)).toBeInTheDocument();

    await userEvent.click(within(table).getByRole("button", { name: "trend" }));
    expect(await screen.findByRole("heading", { name: /Reporte del laboratorio/ })).toBeInTheDocument();
    expect(window.location.search).toBe("?report=trend");
    expect(screen.getByText("Drawdown p95 28 % > 25 %")).toBeInTheDocument();
    expect(screen.getByText(/nivel 1: 20 %/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Resultados por ventana del walk-forward" })).toHaveTextContent("n=20");
    expect(screen.getByText(/1,000 simulaciones de 365 días/)).toBeInTheDocument();
    expect(screen.getByText(/Crecimiento de 1 USDT/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Todos los reportes/ }));
    expect(await screen.findByRole("heading", { name: "Backtests y reportes" })).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("abre el reporte indicado en ?report= y muestra el error si no existe", async () => {
    window.history.replaceState(null, "", "/backtests?report=nope");
    renderView(fakeFetcher({ "GET /lab/reports": [], "GET /backtests": [] }));
    expect(await screen.findByRole("alert")).toHaveTextContent("sin ruta GET /lab/reports/nope");
  });

  it("muestra el p95 en texto cuando el reporte no trae histograma", async () => {
    window.history.replaceState(null, "", "/backtests?report=trend");
    renderView(
      fakeFetcher({
        "GET /lab/reports": [],
        "GET /lab/reports/trend": { ...DETAIL, drawdown_histogram: null, checks: [], reasons: ["r1"], windows: [], equity: [] },
        "GET /backtests": [],
      }),
    );
    expect(await screen.findByText(/no trae la curva diaria/)).toBeInTheDocument();
    expect(screen.getByText("r1")).toBeInTheDocument();
    expect(screen.getByText("El reporte no trae ventanas.")).toBeInTheDocument();
    expect(screen.getByText("El reporte no trae curva de equity.")).toBeInTheDocument();
  });

  it("muestra los estados vacíos y el error de la lista", async () => {
    renderView(
      fakeFetcher({
        "GET /lab/reports": { status: 500, body: { detail: "caída" } },
        "GET /backtests": [],
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("caída");
    expect(screen.getByText("No has encolado backtests.")).toBeInTheDocument();
  });

  it("encola un backtest y recarga la lista", async () => {
    let jobs: unknown[] = [];
    const fetcher = fakeFetcher({
      "GET /lab/reports": [],
      "GET /backtests": () => ({ body: jobs }),
      "POST /backtests": (init: RequestInit | undefined) => {
        const body = JSON.parse(String(init?.body));
        jobs = [{ ...JOB, status: "queued", error: null, config: body.config }];
        return { status: 201, body: jobs[0] };
      },
    });
    renderView(fetcher);
    expect(await screen.findByText("Todavía no hay reportes del laboratorio.")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("ETHUSDT"));
    await userEvent.click(screen.getByLabelText("Kronos"));
    await userEvent.selectOptions(screen.getByLabelText("Temporalidad"), "4h");
    await userEvent.selectOptions(screen.getByLabelText("Modo"), "in_sample");
    await userEvent.type(screen.getByLabelText("Capital inicial (USDT, opcional)"), "750");
    await userEvent.click(screen.getByRole("button", { name: "Encolar backtest" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Backtest encolado (12345678)");
    expect(await screen.findByText("En cola")).toBeInTheDocument();
    const post = fetcher.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post![1] as RequestInit).body)).config).toMatchObject({
      symbols: ["BTCUSDT"],
      strategies: ["trend", "kronos"],
      timeframe: "4h",
      mode: "in_sample",
      initial_capital: "750",
    });
  });

  it("valida el formulario y muestra el error de la API", async () => {
    renderView(
      fakeFetcher({
        "GET /lab/reports": [],
        "GET /backtests": [],
        "POST /backtests": { status: 403, body: { detail: "sin permiso" } },
      }),
    );
    await screen.findByText("Todavía no hay reportes del laboratorio.");
    await userEvent.click(screen.getByLabelText("BTCUSDT"));
    await userEvent.click(screen.getByLabelText("ETHUSDT"));
    await userEvent.click(screen.getByRole("button", { name: "Encolar backtest" }));
    expect(screen.getByText("Elige al menos un instrumento")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("BTCUSDT"));
    await userEvent.click(screen.getByRole("button", { name: "Encolar backtest" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("sin permiso"));
  });
});
