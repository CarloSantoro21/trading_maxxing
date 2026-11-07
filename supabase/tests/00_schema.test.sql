-- Estructura del esquema: tablas, enums, RLS activado, triggers y publicación de Realtime.
begin;
create extension if not exists pgtap with schema extensions;

select plan(25);

-- Tablas
select has_table('public', 'profiles', 'existe profiles');
select has_table('public', 'goals', 'existe goals');
select has_table('public', 'bots', 'existe bots');
select has_table('public', 'trades', 'existe trades');
select has_table('public', 'equity_snapshots', 'existe equity_snapshots');
select has_table('public', 'backtests', 'existe backtests');

-- Enums con los mismos valores que los contratos de Python
select enum_has_labels('public', 'horizon', array['short', 'medium', 'long'], 'valores de horizon');
select enum_has_labels('public', 'direction', array['long', 'short', 'flat'], 'valores de direction');
select enum_has_labels('public', 'order_side', array['buy', 'sell'], 'valores de order_side');
select enum_has_labels('public', 'bot_status', array['stopped', 'running', 'paused', 'killed'], 'valores de bot_status');
select enum_has_labels('public', 'backtest_status', array['queued', 'running', 'done', 'failed'], 'valores de backtest_status');

-- RLS activado en todas las tablas
select is(
  (select count(*)::int from pg_tables
   where schemaname = 'public'
     and tablename in ('profiles', 'goals', 'bots', 'trades', 'equity_snapshots', 'backtests')
     and rowsecurity),
  6,
  'RLS activado en las 6 tablas'
);
select is(
  (select count(*)::int from pg_tables where schemaname = 'public' and not rowsecurity),
  0,
  'ninguna tabla de public queda sin RLS'
);

-- Llaves foráneas
select fk_ok('public', 'profiles', 'user_id', 'auth', 'users', 'id');
select fk_ok('public', 'bots', 'profile_id', 'public', 'profiles', 'id');
select fk_ok('public', 'trades', 'bot_id', 'public', 'bots', 'id');

-- Triggers de updated_at
select has_trigger('public', 'profiles', 'profiles_set_updated_at', 'trigger updated_at en profiles');
select has_trigger('public', 'trades', 'trades_set_updated_at', 'trigger updated_at en trades');
select has_trigger('public', 'backtests', 'backtests_set_updated_at', 'trigger updated_at en backtests');

-- Índices en columnas de filtro
select has_index('public', 'trades', 'trades_bot_id_opened_at_idx', 'índice de trades por bot y fecha');
select has_index('public', 'bots', 'bots_profile_id_idx', 'índice de bots por perfil');

-- Realtime
select ok(
  exists (select 1 from pg_publication_tables
          where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'trades'),
  'trades está en supabase_realtime'
);
select ok(
  exists (select 1 from pg_publication_tables
          where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'equity_snapshots'),
  'equity_snapshots está en supabase_realtime'
);
select ok(
  exists (select 1 from pg_publication_tables
          where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'bots'),
  'bots está en supabase_realtime'
);
select ok(
  not exists (select 1 from pg_publication_tables
              where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'profiles'),
  'profiles no se publica en Realtime'
);

select * from finish();
rollback;
