-- Ledger de referencia: reference_trades, reference_equity, shadow_days_clean y RLS (#57).
-- La primera parte corre como postgres; luego se cambia a authenticated, anon y service_role.
begin;
create extension if not exists pgtap with schema extensions;

select plan(36);

-- ===========================================================================
-- Esquema
-- ===========================================================================
select has_table('public', 'reference_trades', 'existe reference_trades');
select has_table('public', 'reference_equity', 'existe reference_equity');
select columns_are('public', 'reference_trades',
  array['id', 'strategy_id', 'started_at', 'state', 'symbol', 'quantity', 'entry_price',
        'exit_price', 'fees', 'pnl', 'ret', 'opened_at', 'closed_at', 'created_at'],
  'columnas de reference_trades');
select columns_are('public', 'reference_equity',
  array['id', 'strategy_id', 'started_at', 'day', 'state', 'equity', 'cash',
        'positions_value', 'drawdown', 'created_at', 'updated_at'],
  'columnas de reference_equity');
select fk_ok('public', 'reference_trades', 'strategy_id', 'public', 'strategies', 'id');
select fk_ok('public', 'reference_equity', 'strategy_id', 'public', 'strategies', 'id');
select is(
  (select count(*)::int from pg_tables
   where schemaname = 'public' and tablename in ('reference_trades', 'reference_equity')
     and rowsecurity),
  2, 'RLS activado en reference_trades y reference_equity'
);
select has_trigger('public', 'reference_equity', 'reference_equity_set_updated_at',
  'trigger updated_at en reference_equity');
select has_index('public', 'reference_trades', 'reference_trades_strategy_closed_idx',
  'índice de trades por estrategia y cierre');
select ok(
  exists (select 1 from pg_publication_tables
          where pubname = 'supabase_realtime' and schemaname = 'public'
            and tablename = 'reference_equity'),
  'reference_equity está en supabase_realtime'
);
select has_function('public', 'shadow_days_clean', array['strategies'],
  'existe la columna calculada shadow_days_clean');

-- ===========================================================================
-- Restricciones (como postgres). kronos está en shadow en el seed.
-- ===========================================================================
select lives_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, fees, pnl,
        ret, opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'BTCUSDT', 0.5, 60000, 61000, 60.5, 439.5, 0.0146, now() - interval '2 days',
             now() - interval '1 day') $$,
  'un trade de referencia válido'
);
select throws_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, fees, pnl,
        ret, opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'BTCUSDT', 0.5, 60000, 61000, 60.5, 439.5, 0.0146, now() - interval '2 days',
             now() - interval '1 day') $$,
  '23505', null, 'el mismo trade (estrategia, símbolo, cierre) no se duplica'
);
select throws_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, pnl, ret,
        opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'SOLUSDT', 1, 100, 101, 1, 0.01, now() - interval '2 days', now()) $$,
  '23514', null, 'símbolo no soportado falla'
);
select throws_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, pnl, ret,
        opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'lab',
             'BTCUSDT', 1, 100, 101, 1, 0.01, now() - interval '2 days', now()) $$,
  '23514', null, 'solo shadow, active o probation tienen ledger de referencia'
);
select throws_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, pnl, ret,
        opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'BTCUSDT', 1, 100, 101, 1, 0.01, now(), now() - interval '1 day') $$,
  '23514', null, 'closed_at antes de opened_at falla'
);
select throws_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, pnl, ret,
        opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'BTCUSDT', 1, 100, 101, 1, 'NaN', now() - interval '1 day', now()) $$,
  '23514', null, 'ret no finito falla'
);
select lives_ok(
  $$ insert into public.reference_equity
       (strategy_id, started_at, day, state, equity, cash, positions_value, drawdown)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', current_date,
             'shadow', 100439.5, 100439.5, 0, 0) $$,
  'un punto de equity válido'
);
select throws_ok(
  $$ insert into public.reference_equity
       (strategy_id, started_at, day, state, equity, cash, drawdown)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', current_date,
             'shadow', 1, 1, 0) $$,
  '23505', null, 'un solo punto por estrategia y día'
);
select throws_ok(
  $$ insert into public.reference_equity
       (strategy_id, started_at, day, state, equity, cash, drawdown)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days',
             current_date - 1, 'shadow', 1, 1, 1.5) $$,
  '23514', null, 'drawdown fuera de [0, 1] falla'
);
select throws_ok(
  $$ insert into public.reference_equity
       (strategy_id, started_at, day, state, equity, cash, drawdown)
     values ('44444444-4444-4444-4444-444444444402', now(), current_date - 10, 'shadow', 1, 1, 0) $$,
  '23514', null, 'un día antes del inicio de la corrida falla'
);

-- shadow_days_clean: kronos entró a shadow hace 5 días; sin métricas cuenta desde ahí.
select ok(
  (select public.shadow_days_clean(s) between 4.99 and 5.01
   from public.strategies s where s.name = 'kronos'),
  'sin métricas, los días en shadow cuentan desde la entrada'
);
update public.strategies
set live_metrics = jsonb_build_object(
  'shadow', jsonb_build_object('clean_since', (now() - interval '2 days')::text))
where name = 'kronos';
select ok(
  (select public.shadow_days_clean(s) between 1.99 and 2.01
   from public.strategies s where s.name = 'kronos'),
  'una falla reinicia la cuenta (clean_since)'
);
select is(
  (select public.shadow_days_clean(s) from public.strategies s where s.name = 'trend'),
  null::double precision, 'fuera de shadow no hay días en shadow'
);

-- ===========================================================================
-- Usuario autenticado: solo lectura
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}', true);
set local role authenticated;

select ok((select count(*) from public.reference_trades) >= 1, 'authenticated lee los trades de referencia');
select ok((select count(*) from public.reference_equity) >= 1, 'authenticated lee la equity de referencia');
select ok(
  (select public.shadow_days_clean(s) is not null from public.strategies s where s.name = 'kronos'),
  'authenticated lee shadow_days_clean'
);
select throws_ok(
  $$ insert into public.reference_equity (strategy_id, started_at, day, state, equity, cash)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days',
             current_date - 1, 'shadow', 1, 1) $$,
  '42501', null, 'authenticated no escribe equity de referencia'
);
select throws_ok(
  $$ delete from public.reference_trades $$,
  '42501', null, 'authenticated no borra trades de referencia'
);

-- ===========================================================================
-- anon: nada
-- ===========================================================================
reset role;
select set_config('request.jwt.claims', '{"role": "anon"}', true);
set local role anon;

select throws_ok($$ select 1 from public.reference_trades $$, '42501', null, 'anon no lee reference_trades');
select throws_ok($$ select 1 from public.reference_equity $$, '42501', null, 'anon no lee reference_equity');

-- ===========================================================================
-- service_role: escribe; los trades son de solo inserción
-- ===========================================================================
reset role;
select set_config('request.jwt.claims', '{"role": "service_role"}', true);
set local role service_role;

select lives_ok(
  $$ insert into public.reference_equity
       (strategy_id, started_at, day, state, equity, cash, positions_value, drawdown)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', current_date,
             'shadow', 99000, 49000, 50000, 0.0143)
     on conflict (strategy_id, day) do update
       set equity = excluded.equity, cash = excluded.cash,
           positions_value = excluded.positions_value, drawdown = excluded.drawdown $$,
  'service_role hace upsert de la equity diaria'
);
select is(
  (select equity from public.reference_equity
   where strategy_id = '44444444-4444-4444-4444-444444444402' and day = current_date),
  99000::numeric, 'el upsert reemplaza el punto del día'
);
select lives_ok(
  $$ insert into public.reference_trades
       (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, fees, pnl,
        ret, opened_at, closed_at)
     values ('44444444-4444-4444-4444-444444444402', now() - interval '5 days', 'shadow',
             'ETHUSDT', 10, 3000, 2900, 59, -1059, -0.0352, now() - interval '3 days',
             now() - interval '2 days')
     on conflict (strategy_id, symbol, closed_at) do nothing $$,
  'service_role inserta trades de referencia'
);
select throws_ok(
  $$ update public.reference_trades set pnl = 0 $$,
  '42501', null, 'service_role no edita trades de referencia'
);
select throws_ok(
  $$ delete from public.reference_trades $$,
  '42501', null, 'service_role no borra trades de referencia'
);

select * from finish();
rollback;
