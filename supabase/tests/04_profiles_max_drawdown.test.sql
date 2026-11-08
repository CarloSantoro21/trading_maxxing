-- profiles.max_drawdown en (0, 1): igual que RiskLimits.from_profile, que exige < 1.
-- Desde 20261109000100 el valor lo deriva el trigger del risk_level, así que lo que se
-- mande en max_drawdown se ignora y nunca queda fuera de rango.
begin;
create extension if not exists pgtap with schema extensions;

select plan(5);

insert into auth.users (id, aud, role, email)
values ('eeeeeeee-0000-0000-0000-000000000001', 'authenticated', 'authenticated', 'dd@test.local');

select lives_ok(
  $$ insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown)
     values ('eeeeeeee-0000-0000-0000-0000000000a1', 'eeeeeeee-0000-0000-0000-000000000001',
             'short', 1, 500, 1) $$,
  'un max_drawdown = 1 en el insert no rompe nada: se reemplaza'
);
select is(
  (select max_drawdown from public.profiles where id = 'eeeeeeee-0000-0000-0000-0000000000a1'),
  0.10, 'queda el drawdown del nivel 1 (10 %)'
);
select lives_ok(
  $$ update public.profiles set max_drawdown = 0 where id = 'eeeeeeee-0000-0000-0000-0000000000a1' $$,
  'actualizar max_drawdown a 0 tampoco rompe nada'
);
select is(
  (select max_drawdown from public.profiles where id = 'eeeeeeee-0000-0000-0000-0000000000a1'),
  0.10, 'y el valor sigue siendo el del nivel'
);
select is(
  (select count(*)::int from public.profiles where max_drawdown >= 1 or max_drawdown <= 0),
  0, 'ningún perfil (incluido el del seed) queda fuera de (0, 1)'
);

select * from finish();
rollback;
