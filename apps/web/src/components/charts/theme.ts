/**
 * Colores de las gráficas por rol (paleta categórica validada: azul y naranja, con su
 * versión para fondo oscuro). Se definen como variables CSS en el contenedor para que el
 * modo oscuro cambie en un solo lugar.
 */
export const vizRootClass =
  "[--tm-series-1:#2a78d6] [--tm-series-2:#eb6834] [--tm-critical:#e34948] [--tm-grid:#e5e5e5] " +
  "[--tm-axis:#52514e] dark:[--tm-series-1:#3987e5] dark:[--tm-series-2:#d95926] " +
  "dark:[--tm-critical:#e66767] dark:[--tm-grid:#2a2a28] dark:[--tm-axis:#c3c2b7]";

export const SERIES_1 = "var(--tm-series-1)";
export const SERIES_2 = "var(--tm-series-2)";
export const CRITICAL = "var(--tm-critical)";
export const GRID = "var(--tm-grid)";
export const AXIS = "var(--tm-axis)";

export const axisProps = {
  stroke: AXIS,
  tick: { fill: AXIS, fontSize: 12 },
  tickLine: false,
} as const;

export const tooltipStyle = {
  contentStyle: {
    background: "var(--background)",
    border: "1px solid var(--tm-grid)",
    borderRadius: 8,
    color: "var(--foreground)",
    fontSize: 12,
  },
} as const;
