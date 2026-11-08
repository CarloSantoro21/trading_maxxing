-- Onboarding simplificado (issue #78): max_drawdown derivado del nivel, capital mínimo de
-- 500 USDT y defaults de horizon e instruments.
begin;
create extension if not exists pgtap with schema extensions;

select plan(17);

select has_trigger('public', 'profiles', 'profiles_derive_max_drawdown',
  'trigger que deriva max_drawdown del risk_level');
select col_default_is('public', 'profiles', 'horizon', 'medium'::public.horizon, 'horizon por defecto es medium');

-- La tabla de niveles (igual a tm_agents.risk.RISK_TABLE).
select results_eq(
  $$ select l, private.risk_level_max_drawdown(l::smallint) from generate_series(1, 5) l order by l $$,
  $$ values (1, 0.10), (2, 0.15), (3, 0.20), (4, 0.30), (5, 0.40) $$,
  'drawdown máximo por nivel: 10, 15, 20, 30 y 40 %'
);
select is(private.risk_level_max_drawdown(6::smallint), null, 'un nivel fuera de 1..5 no tiene drawdown');

select is(
  (select max_drawdown from public.profiles where id = '22222222-2222-2222-2222-222222222222'),
  0.20, 'el perfil del seed (nivel 3) queda con 20 %'
);

insert into auth.users (id, aud, role, email) values
  ('f0000000-0000-0000-0000-000000000001', 'authenticated', 'authenticated', 'rl1@test.local'),
  ('f0000000-0000-0000-0000-000000000002', 'authenticated', 'authenticated', 'rl2@test.local');

-- Insert mínimo, como el nuevo onboarding: solo nivel y capital.
select lives_ok(
  $$ insert into public.profiles (id, user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-0000000000a1', 'f0000000-0000-0000-0000-000000000001', 1, 500) $$,
  'un perfil con solo risk_level y capital (500, el mínimo) se inserta'
);
select results_eq(
  $$ select horizon::text, instruments, max_drawdown from public.profiles
     where id = 'f0000000-0000-0000-0000-0000000000a1' $$,
  $$ values ('medium', array['BTCUSDT', 'ETHUSDT'], 0.10) $$,
  'defaults: medium, BTCUSDT y ETHUSDT, y drawdown del nivel 1'
);

-- Cambiar el nivel recalcula el drawdown; mandar otro drawdown no tiene efecto.
update public.profiles set risk_level = 4 where id = 'f0000000-0000-0000-0000-0000000000a1';
select is(
  (select max_drawdown from public.profiles where id = 'f0000000-0000-0000-0000-0000000000a1'),
  0.30, 'subir al nivel 4 deja 30 %'
);
update public.profiles set max_drawdown = 0.05 where id = 'f0000000-0000-0000-0000-0000000000a1';
select is(
  (select max_drawdown from public.profiles where id = 'f0000000-0000-0000-0000-0000000000a1'),
  0.30, 'un max_drawdown explícito se ignora'
);
update public.profiles set capital = 750 where id = 'f0000000-0000-0000-0000-0000000000a1';
select is(
  (select max_drawdown from public.profiles where id = 'f0000000-0000-0000-0000-0000000000a1'),
  0.30, 'cambiar otra columna conserva el drawdown del nivel'
);

-- Upsert por user_id, como lo hace el frontend.
insert into public.profiles (user_id, risk_level, capital)
values ('f0000000-0000-0000-0000-000000000001', 2, 600)
on conflict (user_id) do update set risk_level = excluded.risk_level, capital = excluded.capital;
select results_eq(
  $$ select risk_level::int, capital, max_drawdown from public.profiles
     where user_id = 'f0000000-0000-0000-0000-000000000001' $$,
  $$ values (2, 600::numeric, 0.15) $$,
  'el upsert del onboarding también recalcula el drawdown'
);

-- Capital mínimo y nivel fuera de rango.
select throws_ok(
  $$ insert into public.profiles (user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-000000000002', 3, 499.99) $$,
  '23514', null, 'capital menor a 500 falla'
);
select throws_ok(
  $$ insert into public.profiles (user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-000000000002', 0, 1000) $$,
  '23514', null, 'risk_level 0 falla con el mismo código que el check'
);
select throws_ok(
  $$ insert into public.profiles (user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-000000000002', 6, 1000) $$,
  '23514', null, 'risk_level 6 falla'
);
select throws_ok(
  $$ insert into public.profiles (user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-000000000002', null, 1000) $$,
  '23514', null, 'risk_level null falla'
);
select throws_ok(
  $$ insert into public.profiles (user_id, risk_level, capital, instruments)
     values ('f0000000-0000-0000-0000-000000000002', 3, 1000, array['SOLUSDT']) $$,
  '23514', null, 'los instrumentos siguen limitados a BTCUSDT y ETHUSDT'
);

-- Con RLS, el usuario crea su perfil solo con nivel y capital.
select set_config('request.jwt.claims',
  '{"sub": "f0000000-0000-0000-0000-000000000002", "role": "authenticated"}', true);
set local role authenticated;
select lives_ok(
  $$ insert into public.profiles (user_id, risk_level, capital)
     values ('f0000000-0000-0000-0000-000000000002', 5, 1000) $$,
  'authenticated crea su perfil con nivel 5 y 1,000 USDT'
);
reset role;

select * from finish();
rollback;
