# Supabase local

Postgres, Auth y Realtime corren en local con la CLI de Supabase (en Docker), sin límites del plan gratuito ni pausas por inactividad. El proyecto en la nube solo se crea para la entrega final.

## Cómo levantarlo

Requisitos: Docker corriendo y la [CLI de Supabase](https://supabase.com/docs/guides/local-development/cli/getting-started) (`brew install supabase/tap/supabase`). En CI se usa la versión 2.117.0.

```bash
supabase start      # levanta todo y aplica migraciones + seed la primera vez
supabase status     # URLs y llaves locales
supabase db reset   # recrea la base: migraciones de supabase/migrations + supabase/seed.sql
supabase test db    # pruebas pgTAP de supabase/tests
supabase stop       # apaga los contenedores (los datos se conservan; --no-backup los borra)
```

Para un cambio de esquema: `supabase migration new <nombre>`, escribe el SQL, `supabase db reset` y `supabase test db`. Las migraciones ya integradas a `develop` no se editan; se agrega una nueva.

## URLs locales

| Servicio | URL |
|---|---|
| API (REST, Auth, Realtime) | http://127.0.0.1:54321 |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Studio | http://127.0.0.1:54323 |
| Mailpit (correos de Auth) | http://127.0.0.1:54324 |

Las llaves (`PUBLISHABLE_KEY`/`ANON_KEY` para el frontend y `SECRET_KEY`/`SERVICE_ROLE_KEY` para los servicios) se ven con `supabase status` (o `supabase status -o env`). Son llaves de demo iguales en cualquier máquina; nunca se usan en la nube.

### Servicios apagados

En `config.toml` quedaron desactivados para ahorrar memoria:

- `storage` (y su protocolo S3 y buckets de vectores): no guardamos archivos en Supabase; las velas históricas van en Parquet (#10).
- `edge_runtime`: la lógica de servidor vive en `services/*` (FastAPI).
- `analytics` (Logflare y Vector): los logs se ven con `docker logs`.

Siguen activos: Postgres, Auth, PostgREST, Realtime, Studio y Mailpit.

## Usuario demo

El seed crea un usuario con correo confirmado, su perfil (mediano plazo, riesgo 3, 10,000 USDT, drawdown máximo de 15 %), una meta, un bot corriendo, tres operaciones (dos cerradas y una abierta), cuatro fotos de equity, un backtest terminado y las tres estrategias del MVP con su historial (`trend` en `active`, aprobada por el usuario demo; `kronos` en `shadow` e `intraday_momentum` en `lab`).

| Correo | Contraseña |
|---|---|
| `demo@trading-maxxing.local` | `demo1234` |

Solo existe en local.

## Esquema

```mermaid
erDiagram
  AUTH_USERS ||--o| PROFILES : "user_id"
  PROFILES ||--o{ GOALS : "profile_id"
  PROFILES ||--o{ BOTS : "profile_id"
  PROFILES ||--o{ BACKTESTS : "profile_id"
  BOTS ||--o{ TRADES : "bot_id"
  BOTS ||--o{ EQUITY_SNAPSHOTS : "bot_id"
  STRATEGIES ||--o{ STRATEGY_TRANSITIONS : "strategy_id"

  PROFILES {
    uuid id PK
    uuid user_id FK
    horizon horizon
    smallint risk_level
    numeric capital
    numeric max_drawdown
    text_array instruments
  }
  GOALS {
    uuid id PK
    numeric target_return
    date deadline
  }
  BOTS {
    uuid id PK
    text name
    bot_status status
    jsonb strategy_config
  }
  TRADES {
    uuid id PK
    text symbol
    order_side side
    numeric quantity
    numeric entry_price
    numeric exit_price
    numeric stop_loss
    numeric take_profit
    numeric fees
    numeric pnl
    jsonb rationale
    timestamptz opened_at
    timestamptz closed_at
  }
  EQUITY_SNAPSHOTS {
    bigint id PK
    timestamptz ts
    numeric equity
    numeric cash
    numeric drawdown
  }
  BACKTESTS {
    uuid id PK
    backtest_status status
    jsonb config
    jsonb report
    text error
  }
  STRATEGIES {
    uuid id PK
    text name UK
    strategy_state state
    jsonb params
    jsonb lab_report
    jsonb live_metrics
    timestamptz state_changed_at
  }
  STRATEGY_TRANSITIONS {
    bigint id PK
    uuid strategy_id FK
    strategy_state from_state
    strategy_state to_state
    text actor
    text reason
    timestamptz created_at
  }
```

Enums (iguales a los contratos de Python en `packages/shared`):

| Tipo | Valores |
|---|---|
| `horizon` | `short`, `medium`, `long` |
| `direction` | `long`, `short`, `flat` (señales dentro de `trades.rationale`) |
| `order_side` | `buy`, `sell` |
| `bot_status` | `stopped`, `running`, `paused`, `killed` |
| `backtest_status` | `queued`, `running`, `done`, `failed` |
| `strategy_state` | `research`, `lab`, `shadow`, `active`, `probation`, `retired` (igual a `StrategyState` de `tm_agents`) |

Restricciones principales:

- `risk_level` entre 1 y 5, `capital > 0`, `max_drawdown` en (0, 1) (estrictamente menor que 1, igual que el `RiskAgent`), un perfil por usuario.
- Símbolos soportados: `BTCUSDT` y `ETHUSDT` (en `profiles.instruments` y `trades.symbol`).
- Proporciones como fracción: `0.15` = 15 %.
- En `trades`: cantidades y precios positivos, `fees >= 0`, `exit_price` y `closed_at` van juntos, `closed_at >= opened_at`, y el stop y el objetivo del lado correcto de la entrada (en una compra, `stop_loss < entry_price < take_profit`).
- En `backtests`: `done` requiere `report` y `failed` requiere `error`.
- Todas las tablas tienen `created_at` y `updated_at` (este último lo mantiene un trigger), salvo `strategy_transitions`, que es de solo inserción.

### Ciclo de vida de las estrategias (`strategies`, `strategy_transitions`)

El estado de cada estrategia es un dato (propuesta, sección 3.3). Transiciones permitidas (`private.is_allowed_strategy_transition`, igual a `ALLOWED_TRANSITIONS` en `tm_agents.registry`):

| Desde | Hacia |
|---|---|
| `research` | `lab`, `retired` |
| `lab` | `shadow`, `research`, `retired` |
| `shadow` | `active` (solo humano), `lab`, `retired` |
| `active` | `probation`, `retired` |
| `probation` | `active` (solo humano), `retired` |
| `retired` | `lab` (revalidación) |

- Una estrategia nueva siempre nace en `research`, y `strategies.state` no se edita directamente: solo cambia al insertar una fila en `strategy_transitions`. El trigger bloquea la fila, exige que `from_state` sea el estado actual (si no, error `40001`), valida la transición y actualiza `state` y `state_changed_at` en la misma transacción. Así el historial siempre está completo.
- `actor` es `'system'` (cambios automáticos) o el `user_id` (UUID) de la persona. Las promociones a `active` no aceptan `'system'`.
- `reason` es obligatoria. El historial es de solo inserción: ni `service_role` puede editarlo o borrarlo, y una estrategia con historial no se borra (se retira).

Realtime publica `trades`, `equity_snapshots`, `bots` y `strategies` (publicación `supabase_realtime`) para que el dashboard se actualice solo.

## Política de RLS

RLS está activado en todas las tablas. Cada usuario solo ve y modifica lo suyo, según `auth.uid()`:

| Tabla | Dueño | El usuario (`authenticated`) puede |
|---|---|---|
| `profiles` | `user_id = auth.uid()` | leer, crear, editar y borrar el suyo |
| `goals` | vía `profile_id` | leer, crear, editar y borrar |
| `bots` | vía `profile_id` | leer, crear, editar (arrancar, detener) y borrar |
| `backtests` | vía `profile_id` | leer, encolar (solo `status = 'queued'` sin reporte) y borrar |
| `trades` | vía `bot_id` → perfil | solo leer |
| `equity_snapshots` | vía `bot_id` → perfil | solo leer |
| `strategies` | compartida (catálogo global) | solo leer (todas) |
| `strategy_transitions` | compartida (catálogo global) | solo leer (todas) |

- La propiedad a través de perfil o bot se resuelve con `private.owns_profile()` y `private.owns_bot()` (security definer, fuera del esquema expuesto por la API).
- **Escrituras del backend**: `trades`, `equity_snapshots`, `strategies`, `strategy_transitions` y el avance y reporte de `backtests` los escriben los servicios (engine, agents, api) con la llave `service_role`, que se salta RLS. Esa llave vive solo en el backend, nunca en el frontend.
- Además de no tener políticas de escritura, `authenticated` no tiene privilegios de INSERT/UPDATE/DELETE sobre `trades` y `equity_snapshots`, ni de UPDATE sobre `backtests`.
- `anon` no tiene ningún privilegio sobre estas tablas: sin sesión no se ve nada.
- Realtime aplica las mismas políticas: cada usuario solo recibe eventos de sus filas.

## Pruebas

`supabase/tests/` tiene pruebas pgTAP que corren con `supabase test db`:

- `00_schema.test.sql`: tablas, enums, RLS activado, llaves foráneas, triggers, índices y publicación de Realtime.
- `01_constraints.test.sql`: cada check (riesgo fuera de rango, capital 0, símbolo no soportado, stop del lado equivocado, etc.) y el trigger de `updated_at`.
- `02_rls.test.sql`: con dos usuarios, A no ve ni modifica las filas de B; el usuario no escribe operaciones ni equity; `anon` no ve nada y `service_role` sí escribe.
- `03_strategies.test.sql`: tablas del ciclo de vida, transiciones permitidas y prohibidas, promociones solo humanas, historial completo, y RLS (lectura para `authenticated`, nada para `anon`, escritura solo de `service_role`).
- `04_profiles_max_drawdown.test.sql`: `max_drawdown = 1` (y 0) se rechaza; 0.99 es válido.

En CI corren en el workflow `DB` (`.github/workflows/db.yml`), solo cuando cambia algo en `supabase/`.
