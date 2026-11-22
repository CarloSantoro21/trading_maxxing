/**
 * Punto de entrada del Worker. Solo exporta el handler por omisión: workerd trata cualquier
 * export con nombre del módulo principal como un entrypoint, así que la app vive en app.ts.
 */

import { createApp } from "./app";

export default createApp();
