-- StrategyRegistry: tablas strategies y strategy_transitions, ciclo de vida y RLS (issue #55).
-- La primera parte corre como postgres; luego se cambia a authenticated, anon y service_role.
begin;
create extension if not exists pgtap with schema extensions;

select plan(46);

-- ===========================================================================
-- Esquema
-- ===========================================================================
select has_table('public', 'strategies', 'existe strategies');
select has_table('public', 'strategy_transitions', 'existe strategy_transitions');
select enum_has_labels(
  'public', 'strategy_state',
  array['research', 'lab', 'shadow', 'active', 'probation', 'retired'],
  'valores de strategy_state (iguales a StrategyState de Python)'
);
select columns_are('public', 'strategies',
  array['id', 'name', 'state', 'params', 'lab_report', 'live_metrics',
        'state_changed_at', 'created_at', 'updated_at'],
  'columnas de strategies');
select columns_are('public', 'strategy_transitions',
  array['id', 'strategy_id', 'from_state', 'to_state', 'actor', 'reason', 'created_at'],
  'columnas de strategy_transitions');
select col_is_unique('public', 'strategies', 'name', 'name es único');
select fk_ok('public', 'strategy_transitions', 'strategy_id', 'public', 'strategies', 'id');
select is(
  (select count(*)::int from pg_tables
   where schemaname = 'public' and tablename in ('strategies', 'strategy_transitions')
     and rowsecurity),
  2, 'RLS activado en strategies y strategy_transitions'
);
select has_trigger('public', 'strategies', 'strategies_set_updated_at', 'trigger updated_at en strategies');
select has_index('public', 'strategy_transitions', 'strategy_transitions_strategy_id_created_at_idx',
  'índice del historial por estrategia y fecha');
select ok(
  exists (select 1 from pg_publication_tables
          where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'strategies'),
  'strategies está en supabase_realtime'
);

-- El seed deja trend en active con su historial completo
select is((select state::text from public.strategies where name = 'trend'), 'active', 'seed: trend en active');
select results_eq(
  $$ select t.from_state::text, t.to_state::text from public.strategy_transitions t
     join public.strategies s on s.id = t.strategy_id
     where s.name = 'trend' order by t.created_at, t.id $$,
  $$ values ('research', 'lab'), ('lab', 'shadow'), ('shadow', 'active') $$,
  'seed: historial completo de trend'
);

-- ===========================================================================
-- Restricciones y ciclo de vida (como postgres)
-- ===========================================================================
insert into public.strategies (id, name)
values ('dddddddd-0000-0000-0000-000000000001', 'test_strategy');

select is(
  (select state::text from public.strategies where id = 'dddddddd-0000-0000-0000-000000000001'),
  'research', 'una estrategia nueva empieza en research'
);
select throws_ok(
  $$ insert into public.strategies (name, state) values ('born_active', 'active') $$,
  '23514', null, 'no se puede crear una estrategia fuera de research'
);
select throws_ok(
  $$ insert into public.strategies (name) values ('test_strategy') $$,
  '23505', null, 'name duplicado falla'
);
select throws_ok(
  $$ insert into public.strategies (name) values ('Bad Name!') $$,
  '23514', null, 'name con formato inválido falla'
);
select throws_ok(
  $$ insert into public.strategies (name, params) values ('arr_params', '[]') $$,
  '23514', null, 'params debe ser un objeto'
);
select throws_ok(
  $$ update public.strategies set state = 'active' where id = 'dddddddd-0000-0000-0000-000000000001' $$,
  '42501', null, 'el estado no se edita directamente'
);
select lives_ok(
  $$ update public.strategies set live_metrics = '{"drawdown": 0.02}'
     where id = 'dddddddd-0000-0000-0000-000000000001' $$,
  'las métricas en vivo sí se editan directamente'
);

-- Transiciones permitidas
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'research', 'lab', 'system', 'al laboratorio') $$,
  'research -> lab'
);
select is(
  (select state::text from public.strategies where id = 'dddddddd-0000-0000-0000-000000000001'),
  'lab', 'la transición actualiza strategies.state'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'lab', 'active', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'atajo') $$,
  '23514', null, 'lab -> active no está permitida'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'research', 'lab', 'system', 'estado viejo') $$,
  '40001', null, 'from_state distinto del estado actual falla'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'lab', 'shadow', 'system', 'pasa criterios') $$,
  'lab -> shadow'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'shadow', 'active', 'system', 'auto') $$,
  '23514', null, 'shadow -> active con actor system falla'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'shadow', 'active',
             'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'aprobada') $$,
  'shadow -> active con actor humano'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'active', 'probation', 'system', 'drawdown > p95') $$,
  'active -> probation automática'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'probation', 'active', 'system', 'auto') $$,
  '23514', null, 'probation -> active con actor system falla'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'probation', 'retired', 'system', 'drawdown > p99') $$,
  'probation -> retired'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'retired', 'lab', 'system', 'revalidación') $$,
  'retired -> lab (revalidación)'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'lab', 'shadow', 'robot', 'actor raro') $$,
  '23514', null, 'actor que no es system ni uuid falla'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'lab', 'shadow', 'system', '   ') $$,
  '23514', null, 'razón vacía falla'
);
select results_eq(
  $$ select from_state::text, to_state::text from public.strategy_transitions
     where strategy_id = 'dddddddd-0000-0000-0000-000000000001' order by created_at, id $$,
  $$ values ('research', 'lab'), ('lab', 'shadow'), ('shadow', 'active'),
            ('active', 'probation'), ('probation', 'retired'), ('retired', 'lab') $$,
  'el historial guarda todas las transiciones válidas y ninguna rechazada'
);
select throws_ok(
  $$ delete from public.strategies where id = 'dddddddd-0000-0000-0000-000000000001' $$,
  '23503', null, 'una estrategia con historial no se borra'
);

-- ===========================================================================
-- Usuario autenticado: solo lectura
-- ===========================================================================
select set_config('request.jwt.claims',
  '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}', true);
set local role authenticated;

select ok((select count(*) from public.strategies) >= 4, 'authenticated lee strategies');
select ok((select count(*) from public.strategy_transitions) >= 6, 'authenticated lee el historial');
select throws_ok(
  $$ insert into public.strategies (name) values ('from_user') $$,
  '42501', null, 'authenticated no crea estrategias'
);
select throws_ok(
  $$ update public.strategies set params = '{}' where name = 'trend' $$,
  '42501', null, 'authenticated no edita estrategias'
);
select throws_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000001', 'lab', 'shadow',
             '11111111-1111-1111-1111-111111111111', 'desde el navegador') $$,
  '42501', null, 'authenticated no inserta transiciones directamente'
);

-- ===========================================================================
-- anon: nada
-- ===========================================================================
reset role;
select set_config('request.jwt.claims', '{"role": "anon"}', true);
set local role anon;

select throws_ok($$ select 1 from public.strategies $$, '42501', null, 'anon no lee strategies');
select throws_ok($$ select 1 from public.strategy_transitions $$, '42501', null, 'anon no lee el historial');

-- ===========================================================================
-- service_role: escribe estrategias y transiciones; el historial es de solo inserción
-- ===========================================================================
reset role;
select set_config('request.jwt.claims', '{"role": "service_role"}', true);
set local role service_role;

select lives_ok(
  $$ insert into public.strategies (id, name, params)
     values ('dddddddd-0000-0000-0000-000000000002', 'svc_strategy', '{"k": 1}') $$,
  'service_role crea estrategias'
);
select lives_ok(
  $$ insert into public.strategy_transitions (strategy_id, from_state, to_state, actor, reason)
     values ('dddddddd-0000-0000-0000-000000000002', 'research', 'lab', 'system', 'al laboratorio') $$,
  'service_role inserta transiciones'
);
select throws_ok(
  $$ update public.strategy_transitions set reason = 'otra' where strategy_id = 'dddddddd-0000-0000-0000-000000000002' $$,
  '42501', null, 'service_role no edita el historial'
);
select throws_ok(
  $$ delete from public.strategy_transitions where strategy_id = 'dddddddd-0000-0000-0000-000000000002' $$,
  '42501', null, 'service_role no borra el historial'
);

select * from finish();
rollback;
