# Checklist de pruebas end-to-end (#28)

Guía para una corrida manual del flujo completo contra el stack local (Supabase +
servicios de Docker + frontend). Reemplaza los pasos "live" del spec de Playwright
cuando se necesita una validación visual o grabar el video de demostración.

## Requisitos previos

- [ ] Node 24 instalado (`.nvmrc`).
- [ ] Supabase CLI instalado y con `supabase start` corriendo (API en `http://127.0.0.1:54321`).
- [ ] `cp .env.example .env` en la raíz y `cp apps/web/.env.example apps/web/.env.local`
      con la llave `anon` que imprimió `supabase status`.
- [ ] `make up` levantó Redis, `api`, `agents`, `engine` y (en Linux/Intel) `forecast`.
      En Mac con MPS: `make up-mps` + `make forecast-mps` en otra terminal.
- [ ] Usuario demo creado por el seed (o `make user EMAIL=... ROLE=member`).
- [ ] Frontend en vivo: `make web` (o `pnpm dev` dentro de `apps/web`).

## Pasos del flujo

1. **Landing → Login**
   - [ ] Abrir <http://localhost:3000/>.
   - [ ] Clic en *Iniciar sesión*; la URL pasa a `/login`.
   - [ ] Capturar `demo@trading-maxxing.local` / `demo1234` y enviar.
   - [ ] Sin perfil previo, redirige a `/onboarding`; con perfil, a `/dashboard`.

2. **Crear perfil (`/onboarding`, wizard de 3 pasos)**
   - [ ] Paso 1 *Capital*: escribir `1000` USDT.
   - [ ] Paso 2 *Nivel de riesgo*: dejar en 3 (Moderado) o elegir otro; verificar que la
         explicación muestra la caída máxima en USDT.
   - [ ] Paso 3 *Metas*: agregar al menos una (p. ej. 15 % a fecha futura) y guardar.
   - [ ] Aparece la pantalla de confirmación ("¡Perfil guardado!") y el botón
         *Ir a mi dashboard* lleva a `/dashboard`.

3. **Iniciar bot (`/dashboard`)**
   - [ ] La tarjeta *Bot* muestra *Aún no tienes bot* y el botón *Crear mi bot*.
   - [ ] Al crearlo aparece la etiqueta *Detenido* y los controles *Arrancar / Detener /
         Paro de emergencia*.
   - [ ] Pulsar *Arrancar*; la etiqueta cambia a *Operando* (equivalente a "corriendo")
         sin recargar la página.

4. **Recibir señal / decisión**
   - [ ] En los logs (`make logs`) aparecen mensajes de `agents` publicando señales en
         `tm:signals` y decisiones en `tm:decisions`.
   - [ ] Si no llegan señales en la ventana esperada, `agents` o `engine` pueden estar
         en modo *offline* (sin datos de testnet): validar `OFFLINE` en `.env`.

5. **Orden en Binance Testnet**
   - [ ] En los logs del servicio `engine` se registra una orden enviada a Binance
         Testnet (símbolo BTC/USDT o ETH/USDT, precio y cantidad) y su fill.
   - [ ] En el dashboard, en la tarjeta *Últimas operaciones*, aparece la fila con la
         pastilla *Compra* / *Venta*, el símbolo, la cantidad y el precio de entrada.
   - [ ] En la tarjeta *Equity* la curva suma un nuevo punto (requiere dos o más
         snapshots; puede tomar un par de minutos).

6. **Trade visible en el dashboard**
   - [ ] La métrica *Equity* refleja el valor nuevo (`capital + P&L no realizado`).
   - [ ] El badge *Live* en la esquina superior derecha indica que Realtime está conectado.
   - [ ] Expandir una operación con *¿Por qué?* muestra la justificación (señal,
         simulación y veto, si aplica).

## Limpieza

- [ ] *Paro de emergencia* desde el dashboard cierra posiciones y marca el bot como
      *Paro de emergencia* (todas las posiciones vendidas a USDT).
- [ ] `make down` detiene los contenedores pero conserva el estado del volumen de Redis.
- [ ] `supabase stop` termina el stack de Supabase local.

## Automatizado con Playwright

- `cd apps/web && pnpm e2e:mocked` repite los pasos 1-6 contra respuestas mockeadas
  (no necesita Supabase ni Docker; corre en CI).
- `cd apps/web && pnpm e2e` repite los pasos contra el stack real; cada paso se omite
  con `test.skip` si no encuentra el servicio.

## Video de demostración

- Grabar una corrida completa (recomendado: QuickTime o Loom, resolución 1280x720,
  10-15 min) con narración breve en cada paso.

**Pendiente: subir video.** Reemplazar esta línea con la URL cuando esté disponible:

```
https://<pendiente-de-subir>
```
