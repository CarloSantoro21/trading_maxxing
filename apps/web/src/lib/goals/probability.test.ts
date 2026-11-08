import { describe, expect, it } from "vitest";
import { fanSeries, probabilityLabel } from "./probability";

describe("probability", () => {
  it("arma la banda p10–p90", () => {
    expect(fanSeries([{ day: "d", p10: 1, p50: 2, p90: 3 }])[0]).toEqual({
      day: "d",
      band: [1, 3],
      p10: 1,
      p50: 2,
      p90: 3,
    });
  });

  it("describe la probabilidad en palabras", () => {
    expect(probabilityLabel(0.9)).toBe("Muy probable");
    expect(probabilityLabel(0.65)).toBe("Probable");
    expect(probabilityLabel(0.5)).toBe("Incierta");
    expect(probabilityLabel(0.25)).toBe("Poco probable");
    expect(probabilityLabel(0.05)).toBe("Muy poco probable");
  });
});
