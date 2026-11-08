-- Restricciones de integridad (checks, defaults y trigger de updated_at).
-- Corre como postgres, así que RLS no interviene: aquí solo se prueban los datos.
begin;
create extension if not exists pgtap with schema extensions;

select plan(27);

insert into auth.users (id, aud, role, email)
values ('aaaaaaaa-0000-0000-0000-000000000001', 'authenticated', 'authenticated', 'constraints@test.local');

-- Perfil válido de referencia
select lives_ok(
  $$ insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown)
     values ('aaaaaaaa-0000-0000-0000-0000000000a1', 'aaaaaaaa-0000-0000-0000-000000000001',
             'short', 1, 500, 0.1) $$,
  'un perfil válido se inserta'
);
select is(
  (select instruments from public.profiles where id = 'aaaaaaaa-0000-0000-0000-0000000000a1'),
  array['BTCUSDT', 'ETHUSDT'],
  'instruments por defecto son BTCUSDT y ETHUSDT'
);

-- profiles: risk_level, capital, max_drawdown, instruments, unicidad por usuario
select throws_ok(
  $$ update public.profiles set risk_level = 0 where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'risk_level 0 falla'
);
select throws_ok(
  $$ update public.profiles set risk_level = 6 where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'risk_level 6 falla'
);
select lives_ok(
  $$ update public.profiles set risk_level = 5 where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  'risk_level 5 es válido'
);
select throws_ok(
  $$ update public.profiles set capital = 0 where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'capital 0 falla'
);
select throws_ok(
  $$ update public.profiles set capital = 499.99 where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'capital menor a 500 falla'
);
-- max_drawdown ya no se captura: el trigger lo deriva de risk_level (06 lo prueba a fondo).
select is(
  (select max_drawdown from public.profiles where id = 'aaaaaaaa-0000-0000-0000-0000000000a1'),
  0.40, 'max_drawdown sale del nivel (5 -> 40 %), no del valor insertado'
);
select throws_ok(
  $$ update public.profiles set instruments = array['DOGEUSDT'] where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'instrumento no soportado falla'
);
select throws_ok(
  $$ update public.profiles set instruments = '{}' where id = 'aaaaaaaa-0000-0000-0000-0000000000a1' $$,
  '23514', null, 'instruments vacío falla'
);
select throws_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('aaaaaaaa-0000-0000-0000-000000000001', 'long', 2, 500, 0.2) $$,
  '23505', null, 'un usuario no puede tener dos perfiles'
);
select throws_ok(
  $$ insert into public.profiles (user_id, horizon, risk_level, capital, max_drawdown)
     values ('aaaaaaaa-0000-0000-0000-000000000001', 'forever', 2, 500, 0.2) $$,
  '22P02', null, 'horizon fuera del enum falla'
);

-- goals
select throws_ok(
  $$ insert into public.goals (profile_id, target_return, deadline)
     values ('aaaaaaaa-0000-0000-0000-0000000000a1', 0, current_date + 30) $$,
  '23514', null, 'target_return 0 falla'
);

-- bots
insert into public.bots (id, profile_id, name)
values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'bot de prueba');

select is(
  (select status from public.bots where id = 'aaaaaaaa-0000-0000-0000-0000000000b1'),
  'stopped'::public.bot_status,
  'un bot nuevo empieza detenido'
);
select throws_ok(
  $$ update public.bots set strategy_config = '[]' where id = 'aaaaaaaa-0000-0000-0000-0000000000b1' $$,
  '23514', null, 'strategy_config debe ser un objeto json'
);

-- trades
select lives_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, stop_loss, take_profit)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000, 59000, 62000) $$,
  'una operación abierta válida se inserta'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'SOLUSDT', 'buy', 1, 100) $$,
  '23514', null, 'símbolo no soportado falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0, 60000) $$,
  '23514', null, 'quantity 0 falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, stop_loss)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000, 61000) $$,
  '23514', null, 'stop_loss por encima de la entrada en una compra falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, take_profit)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'ETHUSDT', 'sell', 1, 3000, 3100) $$,
  '23514', null, 'take_profit por encima de la entrada en una venta falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, exit_price)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000, 61000) $$,
  '23514', null, 'exit_price sin closed_at falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, exit_price, opened_at, closed_at)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000, 61000,
             now(), now() - interval '1 hour') $$,
  '23514', null, 'closed_at antes de opened_at falla'
);
select throws_ok(
  $$ insert into public.trades (bot_id, symbol, side, quantity, entry_price, fees)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 'BTCUSDT', 'buy', 0.01, 60000, -1) $$,
  '23514', null, 'fees negativas fallan'
);

-- equity_snapshots
select throws_ok(
  $$ insert into public.equity_snapshots (bot_id, equity, cash, drawdown)
     values ('aaaaaaaa-0000-0000-0000-0000000000b1', 1000, 1000, 1.2) $$,
  '23514', null, 'drawdown mayor a 1 falla'
);

-- backtests
select throws_ok(
  $$ insert into public.backtests (profile_id, status, config)
     values ('aaaaaaaa-0000-0000-0000-0000000000a1', 'done', '{}') $$,
  '23514', null, 'un backtest done sin reporte falla'
);
select throws_ok(
  $$ insert into public.backtests (profile_id, status, config)
     values ('aaaaaaaa-0000-0000-0000-0000000000a1', 'failed', '{}') $$,
  '23514', null, 'un backtest failed sin error falla'
);

-- updated_at se actualiza con el trigger. now() es fijo dentro de la transacción,
-- así que la fila se inserta con un updated_at viejo y luego se modifica.
insert into public.goals (id, profile_id, target_return, deadline, created_at, updated_at)
values ('aaaaaaaa-0000-0000-0000-0000000000c1', 'aaaaaaaa-0000-0000-0000-0000000000a1', 0.1,
        current_date + 90, now() - interval '1 day', now() - interval '1 day');
update public.goals set target_return = 0.2 where id = 'aaaaaaaa-0000-0000-0000-0000000000c1';
select is(
  (select updated_at from public.goals where id = 'aaaaaaaa-0000-0000-0000-0000000000c1'),
  now(),
  'el trigger actualiza updated_at'
);

select * from finish();
rollback;
