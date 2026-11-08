-- Portafolio de referencia por estrategia y modo shadow (issue #57).
--
-- Cada estrategia en shadow, active o probation tiene un ledger de referencia a nivel app:
-- corre a tamaño completo (100 % del sleeve, 100,000 USDT nominales) con las mismas señales
-- que reciben los bots, con fills simulados al cierre de la vela y 0.1 % de comisión. Es el
-- portafolio con el que el LifecycleMonitor juzga a la estrategia (propuesta, sección 3.4),
-- no el P&L de ningún usuario. Lo escribe tm_agents.reference.ReferenceService.
--
--   reference_trades: trades cerrados del ledger (de posición 0 a posición 0).
--   reference_equity: equity del ledger al final de cada día UTC.
--
-- Las métricas en vivo (drawdown, Sharpe, días en shadow sin fallas...) van en
-- strategies.live_metrics. started_at separa las corridas de una misma estrategia: una
-- corrida empieza cada vez que la estrategia entra a shadow.

-- ---------------------------------------------------------------------------
-- reference_trades
-- ---------------------------------------------------------------------------
create table public.reference_trades (
  id           bigint generated always as identity primary key,
  strategy_id  uuid not null references public.strategies (id) on delete cascade,
  -- Inicio de la corrida del ledger (entrada a shadow).
  started_at   timestamptz not null,
  -- Estado de la estrategia cuando se cerró el trade.
  state        public.strategy_state not null,
  symbol       text not null,
  quantity     numeric not null,
  entry_price  numeric not null,
  exit_price   numeric not null,
  fees         numeric not null default 0,
  pnl          numeric not null,
  -- pnl / costo de entrada (con comisiones): el retorno que usa el LifecycleMonitor.
  ret          double precision not null,
  opened_at    timestamptz not null,
  closed_at    timestamptz not null,
  created_at   timestamptz not null default now(),

  constraint reference_trades_symbol_supported check (private.is_supported_symbol(symbol)),
  constraint reference_trades_state_tracked check (state in ('shadow', 'active', 'probation')),
  constraint reference_trades_quantity_positive check (quantity > 0),
  constraint reference_trades_prices_positive check (entry_price > 0 and exit_price > 0),
  constraint reference_trades_fees_non_negative check (fees >= 0),
  constraint reference_trades_ret_finite check (ret >= -1 and ret < 'infinity'::double precision),
  constraint reference_trades_closed_after_opened check (closed_at >= opened_at),
  constraint reference_trades_opened_in_run check (opened_at >= started_at),
  -- Idempotencia: reintentar el mismo trade no lo duplica.
  constraint reference_trades_unique unique (strategy_id, symbol, closed_at)
);

comment on table public.reference_trades is 'Trades cerrados del ledger de referencia de cada estrategia (sin capital real). Solo el backend (service role) los escribe.';
comment on column public.reference_trades.started_at is 'Inicio de la corrida del ledger (entrada a shadow).';
comment on column public.reference_trades.ret is 'pnl / costo de entrada, con comisiones.';

create index reference_trades_strategy_closed_idx
  on public.reference_trades (strategy_id, closed_at desc);

-- ---------------------------------------------------------------------------
-- reference_equity
-- ---------------------------------------------------------------------------
create table public.reference_equity (
  id               bigint generated always as identity primary key,
  strategy_id      uuid not null references public.strategies (id) on delete cascade,
  started_at       timestamptz not null,
  day              date not null,
  state            public.strategy_state not null,
  equity           numeric not null,
  cash             numeric not null,
  positions_value  numeric not null default 0,
  -- Drawdown respecto al máximo del equity de la corrida, como fracción.
  drawdown         numeric not null default 0,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint reference_equity_state_tracked check (state in ('shadow', 'active', 'probation')),
  constraint reference_equity_equity_non_negative check (equity >= 0),
  constraint reference_equity_cash_non_negative check (cash >= 0),
  constraint reference_equity_positions_non_negative check (positions_value >= 0),
  constraint reference_equity_drawdown_range check (drawdown >= 0 and drawdown <= 1),
  constraint reference_equity_day_in_run check (day >= (started_at at time zone 'UTC')::date),
  -- Un punto por estrategia y día (el backend hace upsert).
  constraint reference_equity_strategy_day_unique unique (strategy_id, day)
);

comment on table public.reference_equity is 'Equity diaria del ledger de referencia de cada estrategia. Solo el backend (service role) la escribe.';
comment on column public.reference_equity.day is 'Día UTC; el equity es el último valor del día.';

-- La restricción única (strategy_id, day) ya crea el índice para leer la curva por estrategia.

create trigger reference_equity_set_updated_at before update on public.reference_equity
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- Días en shadow sin fallas (para la página /strategies, #58).
--
-- Columna calculada de PostgREST: GET /strategies?select=*,shadow_days_clean. Cuenta desde
-- live_metrics.shadow.clean_since (entrada a shadow o última falla, la más reciente, lo
-- escribe el ReferenceService) o, si todavía no hay métricas, desde state_changed_at. Es
-- null si la estrategia no está en shadow. La promoción exige al menos 7 días.
-- ---------------------------------------------------------------------------
create function public.shadow_days_clean(s public.strategies)
returns double precision
language sql
stable
set search_path = ''
as $$
  select case
    when s.state <> 'shadow' then null
    else greatest(
      0,
      extract(epoch from (
        now() - greatest(
          s.state_changed_at,
          coalesce((s.live_metrics -> 'shadow' ->> 'clean_since')::timestamptz, s.state_changed_at)
        )
      )) / 86400.0
    )
  end;
$$;

comment on function public.shadow_days_clean(public.strategies) is 'Días que lleva la estrategia en shadow sin fallas (null si no está en shadow).';

revoke all on function public.shadow_days_clean(public.strategies) from public, anon;
grant execute on function public.shadow_days_clean(public.strategies) to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- RLS y privilegios: lectura para usuarios autenticados, escritura solo del backend.
-- ---------------------------------------------------------------------------
alter table public.reference_trades enable row level security;
alter table public.reference_equity enable row level security;

revoke all on public.reference_trades, public.reference_equity from anon;
revoke insert, update, delete, truncate
  on public.reference_trades, public.reference_equity from authenticated;
-- Los trades de referencia son de solo inserción, también para el backend.
revoke update, delete, truncate on public.reference_trades from service_role;

create policy reference_trades_select_authenticated on public.reference_trades
  for select to authenticated
  using (true);

create policy reference_equity_select_authenticated on public.reference_equity
  for select to authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- Realtime: la página /strategies (#58) ve la curva de referencia al momento.
-- ---------------------------------------------------------------------------
alter publication supabase_realtime add table public.reference_equity;
