import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Home from "./page";

describe("Home", () => {
  it("muestra el nombre del proyecto", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { name: "trading_maxxing" })).toBeInTheDocument();
  });
});
