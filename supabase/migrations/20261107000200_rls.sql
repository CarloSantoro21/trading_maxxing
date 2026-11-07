-- Row Level Security: cada usuario solo ve y modifica sus propios datos.
--
-- Propiedad:
--   profiles          -> user_id = auth.uid()
--   goals, bots,
--   backtests         -> a través de profiles.user_id
--   trades,
--   equity_snapshots  -> a través de bots -> profiles.user_id
--
-- Escrituras del backend: trades y equity_snapshots solo los escriben los servicios
-- (engine, agents, api) con la llave service_role, que se salta RLS. Los roles anon y
-- authenticated no tienen políticas de escritura ni privilegios de INSERT/UPDATE/DELETE
-- sobre esas tablas. El rol anon no tiene ninguna política, así que no ve nada.

-- Helpers de propiedad. Son security definer para resolver la cadena bot -> profile
-- sin volver a evaluar RLS en cada nivel; solo devuelven un booleano sobre auth.uid().
create function private.owns_profile(target_profile_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.profiles p
    where p.id = target_profile_id
      and p.user_id = (select auth.uid())
  );
$$;

create function private.owns_bot(target_bot_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.bots b
    join public.profiles p on p.id = b.profile_id
    where b.id = target_bot_id
      and p.user_id = (select auth.uid())
  );
$$;

revoke all on function private.owns_profile(uuid) from public;
revoke all on function private.owns_bot(uuid) from public;
grant usage on schema private to authenticated, service_role;
grant execute on function private.owns_profile(uuid) to authenticated, service_role;
grant execute on function private.owns_bot(uuid) to authenticated, service_role;

alter table public.profiles enable row level security;
alter table public.goals enable row level security;
alter table public.bots enable row level security;
alter table public.trades enable row level security;
alter table public.equity_snapshots enable row level security;
alter table public.backtests enable row level security;

-- Defensa en profundidad: anon no toca ninguna tabla y authenticated no escribe
-- trades ni equity (aunque alguien agregue una política por error).
revoke all on public.profiles, public.goals, public.bots, public.trades,
  public.equity_snapshots, public.backtests from anon;
revoke insert, update, delete, truncate on public.trades, public.equity_snapshots from authenticated;
revoke update, truncate on public.backtests from authenticated;

-- profiles ------------------------------------------------------------------
create policy profiles_select_own on public.profiles
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy profiles_insert_own on public.profiles
  for insert to authenticated
  with check (user_id = (select auth.uid()));

create policy profiles_update_own on public.profiles
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

create policy profiles_delete_own on public.profiles
  for delete to authenticated
  using (user_id = (select auth.uid()));

-- goals ---------------------------------------------------------------------
create policy goals_select_own on public.goals
  for select to authenticated
  using (private.owns_profile(profile_id));

create policy goals_insert_own on public.goals
  for insert to authenticated
  with check (private.owns_profile(profile_id));

create policy goals_update_own on public.goals
  for update to authenticated
  using (private.owns_profile(profile_id))
  with check (private.owns_profile(profile_id));

create policy goals_delete_own on public.goals
  for delete to authenticated
  using (private.owns_profile(profile_id));

-- bots ----------------------------------------------------------------------
-- El usuario crea, configura, arranca/detiene y borra sus bots.
create policy bots_select_own on public.bots
  for select to authenticated
  using (private.owns_profile(profile_id));

create policy bots_insert_own on public.bots
  for insert to authenticated
  with check (private.owns_profile(profile_id));

create policy bots_update_own on public.bots
  for update to authenticated
  using (private.owns_profile(profile_id))
  with check (private.owns_profile(profile_id));

create policy bots_delete_own on public.bots
  for delete to authenticated
  using (private.owns_profile(profile_id));

-- trades (solo lectura para el usuario) -------------------------------------
create policy trades_select_own on public.trades
  for select to authenticated
  using (private.owns_bot(bot_id));

-- equity_snapshots (solo lectura para el usuario) ---------------------------
create policy equity_snapshots_select_own on public.equity_snapshots
  for select to authenticated
  using (private.owns_bot(bot_id));

-- backtests -----------------------------------------------------------------
-- El usuario lee y encola sus backtests (solo en estado queued y sin reporte) y puede
-- borrarlos. El avance, el reporte y los errores los escribe el backend.
create policy backtests_select_own on public.backtests
  for select to authenticated
  using (private.owns_profile(profile_id));

create policy backtests_insert_own on public.backtests
  for insert to authenticated
  with check (
    private.owns_profile(profile_id)
    and status = 'queued'
    and report is null
    and error is null
    and started_at is null
    and finished_at is null
  );

create policy backtests_delete_own on public.backtests
  for delete to authenticated
  using (private.owns_profile(profile_id));
