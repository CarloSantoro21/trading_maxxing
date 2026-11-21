// El JSON generado pesa cientos de KB; se declara como `unknown` para que tsc no infiera
// su tipo (lab-reports.ts lo convierte a LabBundle).
declare module "*.json" {
  const value: unknown;
  export default value;
}
