-- app_roles (issue #67): estructura, RLS (cada quien lee su rol, solo service_role escribe)
-- y transiciones del ciclo de vida hechas por un admin.
begin;
create extension if not exists pgtap with schema extensions;

select plan(25);

-- ---------------------------------------------------------------------------
-- Estructura
-- ---------------------------------------------------------------------------
select has_table('public', 'app_roles', 'existe app_roles');
select enum_has_labels('public', 'app_role', array['admin', 'member'], 'valores de app_role');
select col_is_pk('public', 'app_roles', 'user_id', 'user_id es la llave primaria');
select fk_ok('public', 'app_roles', 'user_id', 'auth', 'users', 'id');
select col_default_is('public', 'app_roles', 'role', 'member'::public.app_role, 'el rol por defecto es member');
select ok(
  (select rowsecurity from pg_tables where schemaname = 'public' and tablename = 'app_roles'),
  'RLS activado en app_roles'
);
select has_trigger('public', 'app_roles', 'app_roles_set_updated_at', 'trigger updated_at en app_roles');
select is(
  (select role::text from public.app_roles where user_id = '11111111-1111-1111-1111-111111111111'),
  'admin', 'el usuario demo del seed es admin'
);

-- Datos: un admin (A), un member (M) y un usuario sin fila (N).
insert into auth.users (id, aud, role, email) values
  ('a1000000-0000-0000-0000-00000000000a', 'authenticated', 'authenticated', 'admin@test.local'),
  ('a2000000-0000-0000-0000-00000000000b', 'authenticated', 'authenticated', 'member@test.local'),
  ('a3000000-0000-0000-0000-00000000000c', 'authenticated', 'authenticated', 'none@test.local');
insert into public.app_roles (user_id, role) values
  ('a1000000-0000-0000-0000-00000000000a', 'admin'),
  ('a2000000-0000-0000-0000-00000000000b', 'member');

select throws_ok(
  $$ insert into public.app_roles (user_id, role) values ('a1000000-0000-0000-0000-00000000000a', 'member') $$,
  '23505', null, 'un usuario tiene un solo rol'
);
select throws_ok(
  $$ insert into public.app_roles (user_id, role) values ('a3000000-0000-0000-0000-00000000000c', 'owner') $$,
  '22P02', null, 'rol fuera del enum falla'
);

-- Estrategia de prueba en shadow (lista para promover).
insert into public.strategies (id, name) values ('a9000000-0000-0000-0000-000000000001', 'roles_test');
insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason) values
  ('a9000000-0000-0000-0000-000000000001', 'research', 'lab', 'system', 'entra al laboratorio'),
  ('a9000000-0000-0000-0000-000000000001', 'lab', 'shadow', 'system', 'pasa los criterios');

-- ===========================================================================
-- Member: lee solo su rol, no escribe roles ni transiciones
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "a2000000-0000-0000-0000-00000000000b", "role": "authenticated"}', true);
set local role authenticated;

select results_eq(
  'select user_id::text, role::text from public.app_roles',
  $$ values ('a2000000-0000-0000-0000-00000000000b', 'member') $$,
  'el member solo ve su propio rol'
);
select throws_ok(
  $$ update public.app_roles set role = 'admin' where user_id = 'a2000000-0000-0000-0000-00000000000b' $$,
  '42501', null, 'el member no se puede promover a admin'
);
select throws_ok(
  $$ insert into public.app_roles (user_id, role) values ('a3000000-0000-0000-0000-00000000000c', 'admin') $$,
  '42501', null, 'el member no crea roles'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'shadow', 'active',
             'a2000000-0000-0000-0000-00000000000b', 'quiero promoverla') $$,
  '42501', null, 'el member no promueve estrategias'
);

reset role;

-- Usuario sin fila en app_roles: se trata como member.
select set_config('request.jwt.claims',
  '{"sub": "a3000000-0000-0000-0000-00000000000c", "role": "authenticated"}', true);
set local role authenticated;

select is_empty('select 1 from public.app_roles', 'un usuario sin rol no ve ninguna fila');
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'shadow', 'retired',
             'a3000000-0000-0000-0000-00000000000c', 'la retiro') $$,
  '42501', null, 'un usuario sin rol no retira estrategias'
);

reset role;

-- ===========================================================================
-- Admin: lee su rol y registra transiciones a su nombre
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "a1000000-0000-0000-0000-00000000000a", "role": "authenticated"}', true);
set local role authenticated;

select results_eq(
  'select role::text from public.app_roles',
  $$ values ('admin') $$,
  'el admin ve su propio rol (y solo el suyo)'
);
select throws_ok(
  $$ update public.app_roles set role = 'admin' where user_id = 'a2000000-0000-0000-0000-00000000000b' $$,
  '42501', null, 'ni el admin edita roles: solo service_role'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'shadow', 'active',
             'a2000000-0000-0000-0000-00000000000b', 'a nombre de otro') $$,
  '42501', null, 'el admin no registra transiciones a nombre de otro'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'shadow', 'lab', 'system', 'como sistema') $$,
  '42501', null, 'el admin no registra transiciones como system'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'shadow', 'active',
             'a1000000-0000-0000-0000-00000000000a', '7 días en shadow sin fallas') $$,
  'el admin promueve shadow -> active'
);
select is(
  (select state::text from public.strategies where name = 'roles_test'),
  'active', 'la promoción del admin cambia el estado'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'active', 'lab',
             'a1000000-0000-0000-0000-00000000000a', 'no permitida') $$,
  '23514', null, 'el admin tampoco se salta las transiciones permitidas'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('a9000000-0000-0000-0000-000000000001', 'active', 'retired',
             'a1000000-0000-0000-0000-00000000000a', 'retiro manual') $$,
  'el admin retira una estrategia'
);
select throws_ok(
  $$ update public.strategies set state = 'active' where name = 'roles_test' $$,
  '42501', null, 'el admin no edita strategies.state directamente'
);

reset role;

select * from finish();
rollback;
