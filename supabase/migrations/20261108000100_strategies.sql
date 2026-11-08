-- StrategyRegistry: estado del ciclo de vida de cada estrategia y su historial (issue #55).
--
-- El código de una estrategia entra por PR; su estado es un dato (propuesta, sección 3.3):
--
--   research → lab → shadow → active → probation → retired
--                ▲                                    │
--                └──────────── revalidación ──────────┘
--
-- Reglas que se aplican aquí y en tm_agents.registry (deben coincidir):
--   * Solo existen las transiciones de private.is_allowed_strategy_transition().
--   * Las promociones a active (shadow → active y probation → active) solo las hace una
--     persona: el actor no puede ser 'system'.
--   * strategies.state solo cambia insertando una fila en strategy_transitions, así que el
--     historial siempre está completo. Una estrategia nueva siempre nace en research.
--   * El historial es de solo inserción: nadie (ni service_role) lo edita ni lo borra.

-- Estados del ciclo de vida. Iguales a tm_agents.strategy_stats.StrategyState.
create type public.strategy_state as enum (
  'research', 'lab', 'shadow', 'active', 'probation', 'retired'
);

-- Transiciones permitidas del ciclo de vida.
--   research  → lab (entra al laboratorio) | retired (se descarta la hipótesis)
--   lab       → shadow (pasa los criterios) | research (vuelve a diseño) | retired (no pasa)
--   shadow    → active (promoción, solo humana) | lab (falla en shadow) | retired
--   active    → probation (degradación) | retired
--   probation → active (se recupera; promoción, solo humana) | retired
--   retired   → lab (revalidación con datos nuevos)
create function private.is_allowed_strategy_transition(
  from_state public.strategy_state,
  to_state public.strategy_state
)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select (from_state::text || '>' || to_state::text) = any (array[
    'research>lab', 'research>retired',
    'lab>shadow', 'lab>research', 'lab>retired',
    'shadow>active', 'shadow>lab', 'shadow>retired',
    'active>probation', 'active>retired',
    'probation>active', 'probation>retired',
    'retired>lab'
  ]);
$$;

-- ---------------------------------------------------------------------------
-- strategies: una fila por estrategia de la biblioteca (el nombre de register_strategy).
-- ---------------------------------------------------------------------------
create table public.strategies (
  id                uuid primary key default gen_random_uuid(),
  name              text not null unique,
  state             public.strategy_state not null default 'research',
  -- Parámetros con los que corre la estrategia, p. ej. {"lookbacks": [20, 50, 100]}.
  params            jsonb not null default '{}'::jsonb,
  -- Último reporte del laboratorio (walk-forward + Monte Carlo + criterios de aprobación).
  lab_report        jsonb,
  -- Métricas en vivo (drawdown, Sharpe de los últimos trades, días en el estado, etc.).
  live_metrics      jsonb not null default '{}'::jsonb,
  state_changed_at  timestamptz not null default now(),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint strategies_name_format check (name ~ '^[a-z][a-z0-9_]{0,63}$'),
  constraint strategies_params_object check (jsonb_typeof(params) = 'object'),
  constraint strategies_lab_report_object
    check (lab_report is null or jsonb_typeof(lab_report) = 'object'),
  constraint strategies_live_metrics_object check (jsonb_typeof(live_metrics) = 'object')
);

comment on table public.strategies is 'Estado del ciclo de vida de cada estrategia. La lee cualquier usuario autenticado; solo el backend (service role) la escribe.';
comment on column public.strategies.state is 'Estado actual. Solo cambia insertando en strategy_transitions.';
comment on column public.strategies.lab_report is 'Último reporte del laboratorio (walk-forward, Monte Carlo y criterios).';
comment on column public.strategies.live_metrics is 'Métricas en vivo que vigila el LifecycleMonitor.';

create index strategies_state_idx on public.strategies (state);

-- ---------------------------------------------------------------------------
-- strategy_transitions: historial de cambios de estado (quién, cuándo y por qué).
-- ---------------------------------------------------------------------------
create table public.strategy_transitions (
  id           bigint generated always as identity primary key,
  -- restrict: una estrategia con historial no se borra; se retira.
  strategy_id  uuid not null references public.strategies (id) on delete restrict,
  from_state   public.strategy_state not null,
  to_state     public.strategy_state not null,
  -- 'system' (LifecycleMonitor, laboratorio) o el user_id (uuid) de quien la aprobó.
  actor        text not null,
  reason       text not null,
  created_at   timestamptz not null default now(),

  constraint strategy_transitions_allowed
    check (private.is_allowed_strategy_transition(from_state, to_state)),
  constraint strategy_transitions_actor_format check (
    actor = 'system'
    or actor ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
  ),
  -- Toda transición a active es una promoción (desde shadow o probation): solo humana.
  constraint strategy_transitions_human_promotion
    check (to_state <> 'active' or actor <> 'system'),
  constraint strategy_transitions_reason_length
    check (char_length(btrim(reason)) between 1 and 1000)
);

comment on table public.strategy_transitions is 'Historial de solo inserción de los cambios de estado de las estrategias.';
comment on column public.strategy_transitions.actor is '''system'' para cambios automáticos o el user_id de la persona que lo hizo.';

create index strategy_transitions_strategy_id_created_at_idx
  on public.strategy_transitions (strategy_id, created_at, id);

-- ---------------------------------------------------------------------------
-- Triggers.
-- ---------------------------------------------------------------------------
create trigger strategies_set_updated_at before update on public.strategies
  for each row execute function private.set_updated_at();

-- Una estrategia nace en research y su estado no se edita directamente: solo cambia desde
-- el trigger de strategy_transitions (profundidad de trigger >= 2).
create function private.guard_strategy_state()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.state <> 'research' then
      raise exception 'una estrategia nueva debe empezar en research (recibido: %)', new.state
        using errcode = '23514';
    end if;
    new.state_changed_at := new.created_at;
  elsif new.state is distinct from old.state and pg_trigger_depth() < 2 then
    raise exception 'el estado de una estrategia solo cambia insertando en strategy_transitions'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger strategies_guard_state before insert or update on public.strategies
  for each row execute function private.guard_strategy_state();

-- Aplica la transición a la estrategia. Bloquea la fila para que dos transiciones
-- concurrentes no partan del mismo estado y exige que from_state sea el estado actual.
create function private.apply_strategy_transition()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  current_state public.strategy_state;
begin
  select s.state into current_state
  from public.strategies s
  where s.id = new.strategy_id
  for update;

  if not found then
    -- La llave foránea también lo rechazaría; aquí se da un mensaje claro.
    raise exception 'no existe la estrategia %', new.strategy_id using errcode = '23503';
  end if;

  if new.from_state is distinct from current_state then
    raise exception 'la estrategia está en % y la transición parte de %',
      current_state, new.from_state
      using errcode = '40001';
  end if;

  if not private.is_allowed_strategy_transition(new.from_state, new.to_state) then
    raise exception 'transición no permitida: % -> %', new.from_state, new.to_state
      using errcode = '23514';
  end if;

  if new.to_state = 'active' and new.actor = 'system' then
    raise exception 'la promoción % -> active requiere un actor humano', new.from_state
      using errcode = '23514';
  end if;

  update public.strategies
  set state = new.to_state,
      state_changed_at = new.created_at
  where id = new.strategy_id;

  return new;
end;
$$;

create trigger strategy_transitions_apply before insert on public.strategy_transitions
  for each row execute function private.apply_strategy_transition();

-- ---------------------------------------------------------------------------
-- RLS y privilegios: lectura para usuarios autenticados, escritura solo del backend.
-- ---------------------------------------------------------------------------
alter table public.strategies enable row level security;
alter table public.strategy_transitions enable row level security;

revoke all on public.strategies, public.strategy_transitions from anon;
revoke insert, update, delete, truncate
  on public.strategies, public.strategy_transitions from authenticated;
-- Historial de solo inserción, también para el backend.
revoke update, delete, truncate on public.strategy_transitions from service_role;

create policy strategies_select_authenticated on public.strategies
  for select to authenticated
  using (true);

create policy strategy_transitions_select_authenticated on public.strategy_transitions
  for select to authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- Realtime: el dashboard (/strategies) ve los cambios de estado al momento.
-- ---------------------------------------------------------------------------
alter publication supabase_realtime add table public.strategies;
