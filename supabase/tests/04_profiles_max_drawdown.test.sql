-- profiles.max_drawdown en (0, 1): igual que RiskLimits.from_profile, que exige < 1.
begin;
create extension if not exists pgtap with schema extensions;

select plan(5);

insert into auth.users (id, aud, role, email)
values ('eeeeeeee-0000-0000-0000-000000000001', 'authenticated', 'authenticated', 'dd@test.local');

select throws_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('eeeeeeee-0000-0000-0000-000000000001', 'short', 1, 500, 1) $$,
  '23514', null, 'max_drawdown = 1 falla'
);
select throws_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('eeeeeeee-0000-0000-0000-000000000001', 'short', 1, 500, 0) $$,
  '23514', null, 'max_drawdown = 0 falla'
);
select lives_ok(
  $$ insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown)
     values ('eeeeeeee-0000-0000-0000-0000000000a1', 'eeeeeeee-0000-0000-0000-000000000001',
             'short', 1, 500, 0.99) $$,
  'max_drawdown = 0.99 es válido'
);
select throws_ok(
  $$ update public.profiles set max_drawdown = 1 where id = 'eeeeeeee-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'actualizar max_drawdown a 1 falla'
);
select is(
  (select count(*)::int from public.profiles where max_drawdown >= 1 or max_drawdown <= 0),
  0, 'ningún perfil (incluido el del seed) queda fuera de (0, 1)'
);

select * from finish();
rollback;
