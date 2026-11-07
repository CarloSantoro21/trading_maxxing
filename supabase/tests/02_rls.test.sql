-- Row Level Security con dos usuarios (A y B) más un tercero sin perfil (C).
-- Los datos se cargan como postgres y luego se cambia al rol authenticated con el
-- JWT de cada usuario, igual que lo hace PostgREST.
begin;
create extension if not exists pgtap with schema extensions;

select plan(33);

-- Usuarios
insert into auth.users (id, aud, role, email) values
  ('a0000000-0000-0000-0000-00000000000a', 'authenticated', 'authenticated', 'a@test.local'),
  ('b0000000-0000-0000-0000-00000000000b', 'authenticated', 'authenticated', 'b@test.local'),
  ('c0000000-0000-0000-0000-00000000000c', 'authenticated', 'authenticated', 'c@test.local');

-- Datos de A
insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown)
values ('a0000000-0000-0000-0000-0000000000a1', 'a0000000-0000-0000-0000-00000000000a', 'short', 2, 1000, 0.1);
insert into public.goals (profile_id, target_return, deadline)
values ('a0000000-0000-0000-0000-0000000000a1', 0.05, current_date + 30);
insert into public.bots (id, profile_id, name)
values ('a0000000-0000-0000-0000-0000000000b1', 'a0000000-0000-0000-0000-0000000000a1', 'bot A');
insert into public.trades (id, bot_id, symbol, side, quantity, entry_price)
values ('a0000000-0000-0000-0000-0000000000c1', 'a0000000-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000);
insert into public.equity_snapshots (bot_id, equity, cash)
values ('a0000000-0000-0000-0000-0000000000b1', 1000, 1000);
insert into public.backtests (profile_id, config)
values ('a0000000-0000-0000-0000-0000000000a1', '{"strategy": "trend"}');

-- Datos de B
insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown)
values ('b0000000-0000-0000-0000-0000000000a1', 'b0000000-0000-0000-0000-00000000000b', 'long', 4, 5000, 0.3);
insert into public.goals (profile_id, target_return, deadline)
values ('b0000000-0000-0000-0000-0000000000a1', 0.2, current_date + 365);
insert into public.bots (id, profile_id, name)
values ('b0000000-0000-0000-0000-0000000000b1', 'b0000000-0000-0000-0000-0000000000a1', 'bot B');
insert into public.trades (id, bot_id, symbol, side, quantity, entry_price)
values ('b0000000-0000-0000-0000-0000000000c1', 'b0000000-0000-0000-0000-0000000000b1', 'ETHUSDT', 'sell', 1, 3000);
insert into public.equity_snapshots (bot_id, equity, cash)
values ('b0000000-0000-0000-0000-0000000000b1', 5000, 5000);
insert into public.backtests (profile_id, config)
values ('b0000000-0000-0000-0000-0000000000a1', '{"strategy": "kronos"}');

-- ===========================================================================
-- Usuario A
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "a0000000-0000-0000-0000-00000000000a", "role": "authenticated"}', true);
set local role authenticated;

-- Lectura: solo lo suyo
select results_eq('select id from public.profiles',
  $$ values ('a0000000-0000-0000-0000-0000000000a1'::uuid) $$, 'A solo ve su perfil');
select results_eq('select profile_id from public.goals',
  $$ values ('a0000000-0000-0000-0000-0000000000a1'::uuid) $$, 'A solo ve sus metas');
select results_eq('select id from public.bots',
  $$ values ('a0000000-0000-0000-0000-0000000000b1'::uuid) $$, 'A solo ve sus bots');
select results_eq('select id from public.trades',
  $$ values ('a0000000-0000-0000-0000-0000000000c1'::uuid) $$, 'A solo ve sus operaciones');
select results_eq('select bot_id from public.equity_snapshots',
  $$ values ('a0000000-0000-0000-0000-0000000000b1'::uuid) $$, 'A solo ve su equity');
select results_eq('select profile_id from public.backtests',
  $$ values ('a0000000-0000-0000-0000-0000000000a1'::uuid) $$, 'A solo ve sus backtests');
select is_empty(
  $$ select 1 from public.trades where bot_id = 'b0000000-0000-0000-0000-0000000000b1' $$,
  'A no ve las operaciones de B aunque filtre por su bot'
);
select is_empty(
  $$ select 1 from public.profiles where user_id = '11111111-1111-1111-1111-111111111111' $$,
  'A no ve el perfil del usuario demo del seed'
);

-- Escritura sobre filas de B: no afecta nada
select results_eq(
  $$ with u as (update public.bots set status = 'killed'
                where id = 'b0000000-0000-0000-0000-0000000000b1' returning 1)
     select count(*)::int from u $$,
  $$ values (0) $$, 'A no puede actualizar el bot de B'
);
select results_eq(
  $$ with d as (delete from public.profiles
                where id = 'b0000000-0000-0000-0000-0000000000a1' returning 1)
     select count(*)::int from d $$,
  $$ values (0) $$, 'A no puede borrar el perfil de B'
);
select results_eq(
  $$ with d as (delete from public.goals
                where profile_id = 'b0000000-0000-0000-0000-0000000000a1' returning 1)
     select count(*)::int from d $$,
  $$ values (0) $$, 'A no puede borrar las metas de B'
);

-- Inserciones a nombre de otro: violan la política
select throws_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('c0000000-0000-0000-0000-00000000000c', 'short', 1, 100, 0.1) $$,
  '42501', null, 'A no puede crear un perfil para otro usuario'
);
select throws_ok(
  $$ insert into public.goals (profile_id, target_return, deadline)
     values ('b0000000-0000-0000-0000-0000000000a1', 0.1, current_date + 10) $$,
  '42501', null, 'A no puede crear metas en el perfil de B'
);
select throws_ok(
  $$ insert into public.bots (profile_id, name)
     values ('b0000000-0000-0000-0000-0000000000a1', 'intruso') $$,
  '42501', null, 'A no puede crear bots en el perfil de B'
);
select throws_ok(
  $$ update public.bots set profile_id = 'b0000000-0000-0000-0000-0000000000a1'
     where id = 'a0000000-0000-0000-0000-0000000000b1' $$,
  '42501', null, 'A no puede mover su bot al perfil de B'
);
select throws_ok(
  $$ insert into public.backtests (profile_id, config)
     values ('b0000000-0000-0000-0000-0000000000a1', '{}') $$,
  '42501', null, 'A no puede encolar backtests en el perfil de B'
);

-- trades y equity son de solo lectura para el usuario (los escribe el backend)
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price)
     values ('a0000000-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 1, 1) $$,
  '42501', null, 'A no puede insertar operaciones ni en su propio bot'
);
select throws_ok(
  $$ update public.trades set pnl = 1000000 where id = 'a0000000-0000-0000-0000-0000000000c1' $$,
  '42501', null, 'A no puede modificar sus operaciones'
);
select throws_ok(
  $$ delete from public.trades where id = 'a0000000-0000-0000-0000-0000000000c1' $$,
  '42501', null, 'A no puede borrar sus operaciones'
);
select throws_ok(
  $$ insert into public.equity_snapshots (bot_id, equity, cash)
     values ('a0000000-0000-0000-0000-0000000000b1', 1, 1) $$,
  '42501', null, 'A no puede insertar equity'
);

-- backtests: el usuario solo encola; el resultado lo escribe el backend
select throws_ok(
  $$ insert into public.backtests (profile_id, status, config, report)
     values ('a0000000-0000-0000-0000-0000000000a1', 'done', '{}', '{"sharpe_oos": 99}') $$,
  '42501', null, 'A no puede insertar un backtest ya terminado'
);
select throws_ok(
  $$ update public.backtests set status = 'running'
     where profile_id = 'a0000000-0000-0000-0000-0000000000a1' $$,
  '42501', null, 'A no puede cambiar el estado de un backtest'
);

-- Lo que sí puede hacer con lo suyo
select lives_ok(
  $$ insert into public.backtests (profile_id, config)
     values ('a0000000-0000-0000-0000-0000000000a1', '{"strategy": "intraday_momentum"}') $$,
  'A puede encolar un backtest en su perfil'
);
select lives_ok(
  $$ insert into public.goals (profile_id, target_return, deadline)
     values ('a0000000-0000-0000-0000-0000000000a1', 0.08, current_date + 60) $$,
  'A puede crear metas en su perfil'
);
select results_eq(
  $$ with u as (update public.bots set status = 'running'
                where id = 'a0000000-0000-0000-0000-0000000000b1' returning 1)
     select count(*)::int from u $$,
  $$ values (1) $$, 'A puede arrancar su bot'
);

reset role;

-- ===========================================================================
-- Usuario B
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "b0000000-0000-0000-0000-00000000000b", "role": "authenticated"}', true);
set local role authenticated;

select results_eq('select id from public.profiles',
  $$ values ('b0000000-0000-0000-0000-0000000000a1'::uuid) $$, 'B solo ve su perfil');
select results_eq('select id from public.trades',
  $$ values ('b0000000-0000-0000-0000-0000000000c1'::uuid) $$, 'B no ve las operaciones de A');
select results_eq(
  $$ select status from public.bots where id = 'b0000000-0000-0000-0000-0000000000b1' $$,
  $$ values ('stopped'::public.bot_status) $$, 'el bot de B sigue intacto'
);

reset role;

-- ===========================================================================
-- Usuario C (autenticado, sin perfil)
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "c0000000-0000-0000-0000-00000000000c", "role": "authenticated"}', true);
set local role authenticated;

select is_empty('select 1 from public.trades', 'C no ve ninguna operación');
select lives_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('c0000000-0000-0000-0000-00000000000c', 'medium', 3, 2000, 0.2) $$,
  'C puede crear su propio perfil'
);

reset role;

-- ===========================================================================
-- anon: sin acceso
-- ===========================================================================
select set_config('request.jwt.claims', '{"role": "anon"}', true);
set local role anon;

select throws_ok('select * from public.profiles', '42501', null, 'anon no puede leer profiles');
select throws_ok('select * from public.trades', '42501', null, 'anon no puede leer trades');

reset role;

-- ===========================================================================
-- service_role (backend): se salta RLS y escribe operaciones
-- ===========================================================================
select set_config('request.jwt.claims', '{"role": "service_role"}', true);
set local role service_role;

select lives_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price)
     values ('a0000000-0000-0000-0000-0000000000b1', 'ETHUSDT', 'buy', 0.1, 3000) $$,
  'service_role puede insertar operaciones'
);

reset role;

select * from finish();
rollback;
