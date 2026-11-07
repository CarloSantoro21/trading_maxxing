-- Supabase Realtime: el dashboard se suscribe a los cambios de operaciones, equity y
-- estado de los bots. Realtime aplica las mismas políticas de RLS, así que cada
-- usuario solo recibe eventos de sus propias filas.
alter publication supabase_realtime add table public.trades, public.equity_snapshots, public.bots;
