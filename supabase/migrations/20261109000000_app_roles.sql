-- Usuarios del equipo y roles (issue #67).
--
-- La app es para un equipo de menos de 10 personas: el registro público está apagado
-- (enable_signup = false en config.toml) y un admin da de alta a cada persona con
-- `make user EMAIL=... ROLE=...` (API admin de Supabase Auth con la llave service_role).
--
-- Roles:
--   * admin  -> además de su portafolio, promueve y retira estrategias y da de alta usuarios.
--   * member -> solo su portafolio. Un usuario sin fila en app_roles se trata como member.
--
-- Cada quien lee su propio rol; solo service_role escribe en app_roles.

create type public.app_role as enum ('admin', 'member');

create table public.app_roles (
  user_id     uuid primary key references auth.users (id) on delete cascade,
  role        public.app_role not null default 'member',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

comment on table public.app_roles is 'Rol de cada usuario del equipo (admin o member). Cada usuario lee el suyo; solo service_role escribe.';

create trigger app_roles_set_updated_at before update on public.app_roles
  for each row execute function private.set_updated_at();

alter table public.app_roles enable row level security;

revoke all on public.app_roles from anon;
revoke insert, update, delete, truncate on public.app_roles from authenticated;

create policy app_roles_select_own on public.app_roles
  for select to authenticated
  using (user_id = (select auth.uid()));

-- ¿El usuario de la sesión es admin? Security definer para que las políticas de otras
-- tablas lo usen sin depender de la política de app_roles.
create function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.app_roles r
    where r.user_id = (select auth.uid())
      and r.role = 'admin'
  );
$$;

revoke all on function private.is_admin() from public;
grant execute on function private.is_admin() to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Ciclo de vida: un admin puede registrar transiciones a su nombre (promover o retirar).
-- ---------------------------------------------------------------------------
-- Hasta ahora solo service_role escribía strategy_transitions. Un admin ahora puede insertar
-- una transición con su propio user_id como actor; las reglas del trigger (transiciones
-- permitidas, from_state igual al estado actual, promociones solo humanas) siguen igual.
grant insert on public.strategy_transitions to authenticated;

create policy strategy_transitions_insert_admin on public.strategy_transitions
  for insert to authenticated
  with check (
    private.is_admin()
    and actor = (select auth.uid())::text
  );

-- El trigger que aplica la transición bloquea y actualiza la fila de strategies, sobre la que
-- authenticated no tiene privilegios de escritura. Corre como su dueño para que la transición
-- de un admin se aplique igual que la de service_role; strategies.state sigue sin poder
-- editarse directamente.
alter function private.apply_strategy_transition() security definer;
