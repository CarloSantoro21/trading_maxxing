-- Auditorías del StrategyAuditorAgent (#108): una por (bot, estrategia, símbolo, modo, vela, trade).
create table public.signal_audits (
  id                uuid primary key default gen_random_uuid(),
  bot_id            uuid not null references public.bots (id) on delete cascade,
  trade_id          uuid references public.trades (id) on delete set null,
  strategy          text not null,
  symbol            text not null,
  close_ts          timestamptz not null,
  mode              text not null check (mode in ('entry', 'exit')),
  decision          text not null check (decision in ('APPROVE', 'REJECT', 'HOLD', 'CLOSE_NOW')),
  veto_reason       text,
  confidence        smallint not null check (confidence between 0 and 100),
  rule_evaluations  jsonb not null default '{}'::jsonb,
  input             jsonb not null default '{}'::jsonb,
  output            jsonb not null default '{}'::jsonb,
  trace_id          text,
  status            text not null default 'ok' check (status in ('ok', 'fail_open', 'timeout', 'error')),
  latency_ms        integer check (latency_ms is null or latency_ms >= 0),
  model             text,
  created_at        timestamptz not null default now(),
  constraint signal_audits_rule_evaluations_object check (jsonb_typeof(rule_evaluations) = 'object'),
  constraint signal_audits_input_object            check (jsonb_typeof(input) = 'object'),
  constraint signal_audits_output_object           check (jsonb_typeof(output) = 'object'),
  constraint signal_audits_unique_key              unique (bot_id, strategy, symbol, mode, close_ts, trade_id)
);

create index signal_audits_bot_created_idx on public.signal_audits (bot_id, created_at desc);
create index signal_audits_strategy_idx    on public.signal_audits (strategy, created_at desc);
create index signal_audits_trade_idx       on public.signal_audits (trade_id) where trade_id is not null;

comment on table public.signal_audits is 'Auditorías del StrategyAuditorAgent: una por (bot, estrategia, símbolo, modo, vela, trade). Solo el backend escribe; el dueño del bot lee las suyas.';
comment on column public.signal_audits.strategy is 'Nombre registrado de la estrategia (trend, intraday_momentum, moving_average, tsmom, kronos, ...).';
comment on column public.signal_audits.mode is 'entry = auditoría previa a abrir el trade; exit = re-evaluación por vela cerrada mientras está abierto.';
comment on column public.signal_audits.trace_id is 'ID del trace en Langfuse para abrir el detalle paso a paso.';

alter table public.signal_audits enable row level security;

create policy "signal_audits service_role writes" on public.signal_audits
  for all to service_role using (true) with check (true);

create policy "signal_audits owner reads" on public.signal_audits
  for select to authenticated
  using (private.owns_bot(bot_id));
