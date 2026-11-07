# web

Frontend de trading_maxxing (Next.js 15, App Router, TypeScript, Tailwind).

```bash
pnpm install
cp .env.example .env.local   # llena la llave anon con `supabase status`
pnpm dev        # http://localhost:3000
pnpm test       # Vitest + React Testing Library
pnpm lint
pnpm typecheck
pnpm build
```

Requiere Node 24 LTS (ver `.nvmrc` en la raíz).

## Variables de entorno

| Variable | Valor local |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | `http://127.0.0.1:54321` (API de `supabase start`) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `anon key` de `supabase status` |

Son públicas: Next.js las incrusta en el bundle durante el build. Nunca pongas aquí la
llave `service_role`. Si faltan, el build pasa igual y la app muestra un aviso.

## Autenticación y onboarding

- **Sitio estático.** En la entrega el frontend se publica con `output: "export"` en
  Cloudflare Pages, así que no hay middleware ni server actions: la sesión de Supabase Auth
  se resuelve en el navegador (`@supabase/ssr` con `createBrowserClient`) dentro de
  `AuthProvider`, y `RequireAuth` redirige a `/login?next=…` si no hay sesión. La seguridad
  real la dan las políticas RLS de la base; la protección de rutas solo evita mostrar
  pantallas que no funcionarían.
- **`/login`**: correo y contraseña. Usuario demo del seed: `demo@trading-maxxing.local` /
  `demo1234`. Tras entrar va a `next` (solo rutas internas), a `/onboarding` si todavía no
  hay perfil o a `/` si ya lo hay.
- **`/onboarding`**: wizard de cuatro pasos (capital y horizonte, riesgo y drawdown,
  instrumentos, metas). Escribe en `profiles` (upsert por `user_id`) y reemplaza las filas de
  `goals` con la sesión del usuario, por lo que RLS limita todo a sus propias filas. Si ya hay
  perfil, el wizard se prellena para editarlo.
- **Validación** con zod en `src/lib/onboarding/schema.ts`, alineada con los checks de
  `supabase/migrations/20261107000100_tables.sql`: capital > 0, riesgo entero de 1 a 5,
  drawdown en (0 %, 100 %] (se guarda como fracción), instrumentos no vacíos dentro de
  {BTCUSDT, ETHUSDT}, retorno objetivo > 0 % y fecha límite futura. Si cambian los checks de
  la base, hay que cambiar ese archivo.

## Pruebas

Las pruebas no usan red: `src/test/fakeSupabase.ts` simula el cliente de Supabase (query
builder encadenable y `auth`) y registra cada llamada para verificar qué se escribe.
