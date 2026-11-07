-- Esquema inicial: perfiles, metas, bots, operaciones, equity y backtests.
-- Las velas históricas no viven aquí (van en Parquet, issue #10).
--
-- Convenciones:
--   * Montos y precios en numeric sin escala fija para no perder precisión.
--   * Proporciones (drawdown, retorno objetivo) como fracción: 0.15 = 15 %.
--   * Todas las tablas tienen created_at y updated_at; updated_at lo mantiene un trigger.

-- Esquema privado para funciones internas. PostgREST solo expone public,
-- así que nada de aquí se puede llamar como RPC.
create schema if not exists private;

-- Símbolos soportados en el MVP (Binance Spot Testnet).
-- Es un check y no un enum para poder agregar pares con una migración sencilla.
create function private.is_supported_symbol(symbol text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select symbol in ('BTCUSDT', 'ETHUSDT');
$$;

-- Trigger genérico que actualiza updated_at en cada UPDATE.
create function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles: perfil de inversión, uno por usuario de Supabase Auth.
-- ---------------------------------------------------------------------------
create table public.profiles (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null unique references auth.users (id) on delete cascade,
  horizon       public.horizon not null,
  risk_level    smallint not null,
  capital       numeric not null,
  max_drawdown  numeric not null,
  instruments   text[] not null default array['BTCUSDT', 'ETHUSDT'],
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  constraint profiles_risk_level_range check (risk_level between 1 and 5),
  constraint profiles_capital_positive check (capital > 0),
  -- Fracción del capital: mayor que 0 y como máximo 1 (100 %).
  constraint profiles_max_drawdown_range check (max_drawdown > 0 and max_drawdown <= 1),
  constraint profiles_instruments_not_empty check (cardinality(instruments) >= 1),
  constraint profiles_instruments_supported
    check (instruments <@ array['BTCUSDT', 'ETHUSDT']::text[])
);

comment on table public.profiles is 'Perfil de inversión del usuario (horizonte, riesgo, capital, drawdown e instrumentos).';
comment on column public.profiles.max_drawdown is 'Drawdown máximo permitido como fracción (0.2 = 20 %). Al rebasarlo, el kill switch detiene el bot.';

-- ---------------------------------------------------------------------------
-- goals: metas de rendimiento de un perfil.
-- ---------------------------------------------------------------------------
create table public.goals (
  id             uuid primary key default gen_random_uuid(),
  profile_id     uuid not null references public.profiles (id) on delete cascade,
  target_return  numeric not null,
  deadline       date not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),

  -- Retorno objetivo como fracción del capital (0.15 = 15 %).
  constraint goals_target_return_positive check (target_return > 0)
);

comment on table public.goals is 'Metas de rendimiento (retorno objetivo y fecha límite) de un perfil.';

create index goals_profile_id_idx on public.goals (profile_id);

-- ---------------------------------------------------------------------------
-- bots: instancia de trading de un perfil.
-- ---------------------------------------------------------------------------
create table public.bots (
  id               uuid primary key default gen_random_uuid(),
  profile_id       uuid not null references public.profiles (id) on delete cascade,
  name             text not null,
  status           public.bot_status not null default 'stopped',
  -- Estrategias activas y su configuración, p. ej.
  -- {"kronos": {"enabled": true, "weight": 0.4}, "trend": {...}}
  strategy_config  jsonb not null default '{}'::jsonb,
  started_at       timestamptz,
  stopped_at       timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint bots_name_length check (char_length(name) between 1 and 100),
  constraint bots_strategy_config_object check (jsonb_typeof(strategy_config) = 'object')
);

comment on table public.bots is 'Bots de trading de un perfil, con su estado y la configuración de estrategias activas.';

create index bots_profile_id_idx on public.bots (profile_id);
create index bots_status_idx on public.bots (status);

-- ---------------------------------------------------------------------------
-- trades: operaciones ejecutadas por un bot. Las escribe el backend (service role).
-- ---------------------------------------------------------------------------
create table public.trades (
  id           uuid primary key default gen_random_uuid(),
  bot_id       uuid not null references public.bots (id) on delete cascade,
  symbol       text not null,
  side         public.order_side not null,
  quantity     numeric not null,
  entry_price  numeric not null,
  exit_price   numeric,
  stop_loss    numeric,
  take_profit  numeric,
  fees         numeric not null default 0,
  pnl          numeric,
  -- Justificación de la decisión: señales de cada agente, pesos del allocator,
  -- resultado del SimulationGate y evaluación del RiskAgent.
  rationale    jsonb not null default '{}'::jsonb,
  opened_at    timestamptz not null default now(),
  closed_at    timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),

  constraint trades_symbol_supported check (private.is_supported_symbol(symbol)),
  constraint trades_quantity_positive check (quantity > 0),
  constraint trades_entry_price_positive check (entry_price > 0),
  constraint trades_exit_price_positive check (exit_price is null or exit_price > 0),
  constraint trades_stop_loss_positive check (stop_loss is null or stop_loss > 0),
  constraint trades_take_profit_positive check (take_profit is null or take_profit > 0),
  constraint trades_fees_non_negative check (fees >= 0),
  constraint trades_rationale_object check (jsonb_typeof(rationale) = 'object'),
  -- Una operación cerrada tiene precio de salida y fecha de cierre; una abierta, ninguno.
  constraint trades_close_consistent
    check ((closed_at is null) = (exit_price is null)),
  constraint trades_closed_after_opened check (closed_at is null or closed_at >= opened_at),
  -- En una compra el stop va por debajo de la entrada y el objetivo por encima; en una venta, al revés.
  constraint trades_stop_loss_side check (
    stop_loss is null
    or (side = 'buy' and stop_loss < entry_price)
    or (side = 'sell' and stop_loss > entry_price)
  ),
  constraint trades_take_profit_side check (
    take_profit is null
    or (side = 'buy' and take_profit > entry_price)
    or (side = 'sell' and take_profit < entry_price)
  )
);

comment on table public.trades is 'Operaciones de un bot. Solo el backend (service role) las escribe; el usuario solo las lee.';
comment on column public.trades.rationale is 'Señales, pesos, simulación previa y evaluación de riesgo que justifican la operación.';

create index trades_bot_id_opened_at_idx on public.trades (bot_id, opened_at desc);
create index trades_symbol_idx on public.trades (symbol);
-- Operaciones abiertas (las que vigila el engine y muestra el dashboard).
create index trades_open_idx on public.trades (bot_id) where closed_at is null;

-- ---------------------------------------------------------------------------
-- equity_snapshots: curva de equity de un bot. Las escribe el backend (service role).
-- ---------------------------------------------------------------------------
create table public.equity_snapshots (
  id               bigint generated always as identity primary key,
  bot_id           uuid not null references public.bots (id) on delete cascade,
  ts               timestamptz not null default now(),
  equity           numeric not null,
  cash             numeric not null,
  positions_value  numeric not null default 0,
  -- Drawdown respecto al máximo histórico de equity, como fracción.
  drawdown         numeric not null default 0,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint equity_snapshots_equity_non_negative check (equity >= 0),
  constraint equity_snapshots_cash_non_negative check (cash >= 0),
  constraint equity_snapshots_drawdown_range check (drawdown >= 0 and drawdown <= 1),
  constraint equity_snapshots_bot_ts_unique unique (bot_id, ts)
);

comment on table public.equity_snapshots is 'Foto periódica del equity de un bot. Solo el backend (service role) la escribe.';

-- La restricción única (bot_id, ts) ya crea el índice para consultar la curva por bot y fecha.

-- ---------------------------------------------------------------------------
-- backtests: jobs de backtest (in-sample, out-of-sample, walk-forward) de un perfil.
-- ---------------------------------------------------------------------------
create table public.backtests (
  id           uuid primary key default gen_random_uuid(),
  profile_id   uuid not null references public.profiles (id) on delete cascade,
  status       public.backtest_status not null default 'queued',
  config       jsonb not null,
  report       jsonb,
  error        text,
  started_at   timestamptz,
  finished_at  timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),

  constraint backtests_config_object check (jsonb_typeof(config) = 'object'),
  constraint backtests_report_object check (report is null or jsonb_typeof(report) = 'object'),
  constraint backtests_done_has_report check (status <> 'done' or report is not null),
  constraint backtests_failed_has_error check (status <> 'failed' or error is not null),
  constraint backtests_finished_after_started
    check (finished_at is null or started_at is null or finished_at >= started_at)
);

comment on table public.backtests is 'Backtests solicitados por el usuario. El usuario los encola; el backend los corre y escribe el reporte.';

create index backtests_profile_id_created_at_idx on public.backtests (profile_id, created_at desc);
create index backtests_status_idx on public.backtests (status);

-- ---------------------------------------------------------------------------
-- Triggers de updated_at.
-- ---------------------------------------------------------------------------
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function private.set_updated_at();
create trigger goals_set_updated_at before update on public.goals
  for each row execute function private.set_updated_at();
create trigger bots_set_updated_at before update on public.bots
  for each row execute function private.set_updated_at();
create trigger trades_set_updated_at before update on public.trades
  for each row execute function private.set_updated_at();
create trigger equity_snapshots_set_updated_at before update on public.equity_snapshots
  for each row execute function private.set_updated_at();
create trigger backtests_set_updated_at before update on public.backtests
  for each row execute function private.set_updated_at();
