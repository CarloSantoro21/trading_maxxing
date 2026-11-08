import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Home from "./page";

describe("Home", () => {
  it("muestra el nombre del proyecto", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { name: "trading_maxxing" })).toBeInTheDocument();
  });

  it("enlaza a login, dashboard, estrategias y onboarding", () => {
    render(<Home />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/login", "/dashboard", "/strategies", "/onboarding"]);
  });
});
