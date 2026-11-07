-- Datos de prueba para desarrollo local. Se cargan con `supabase db reset`.
-- Usuario demo: demo@trading-maxxing.local / demo1234 (solo local, nunca en la nube).

-- Usuario de Supabase Auth con correo confirmado.
-- Las columnas de tokens van como cadena vacía porque GoTrue no acepta NULL en ellas.
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change, email_change_token_new
) values (
  '00000000-0000-0000-0000-000000000000',
  '11111111-1111-1111-1111-111111111111',
  'authenticated',
  'authenticated',
  'demo@trading-maxxing.local',
  extensions.crypt('demo1234', extensions.gen_salt('bf')),
  now(),
  '{"provider": "email", "providers": ["email"]}',
  '{"name": "Demo"}',
  now(),
  now(),
  '', '', '', ''
);

insert into auth.identities (
  id, user_id, provider_id, identity_data, provider, last_sign_in_at, created_at, updated_at
) values (
  gen_random_uuid(),
  '11111111-1111-1111-1111-111111111111',
  '11111111-1111-1111-1111-111111111111',
  jsonb_build_object(
    'sub', '11111111-1111-1111-1111-111111111111',
    'email', 'demo@trading-maxxing.local',
    'email_verified', true
  ),
  'email',
  now(),
  now(),
  now()
);

-- Perfil: mediano plazo, riesgo 3, 10,000 USDT, drawdown máximo de 15 %.
insert into public.profiles (id, user_id, horizon, risk_level, capital, max_drawdown, instruments)
values (
  '22222222-2222-2222-2222-222222222222',
  '11111111-1111-1111-1111-111111111111',
  'medium', 3, 10000, 0.15, array['BTCUSDT', 'ETHUSDT']
);

-- Meta: 12 % en un año.
insert into public.goals (profile_id, target_return, deadline)
values ('22222222-2222-2222-2222-222222222222', 0.12, (current_date + interval '1 year')::date);

-- Bot con las tres estrategias del MVP.
insert into public.bots (id, profile_id, name, status, strategy_config, started_at)
values (
  '33333333-3333-3333-3333-333333333333',
  '22222222-2222-2222-2222-222222222222',
  'Comité BTC/ETH',
  'running',
  '{
    "kronos":           {"enabled": true, "weight": 0.40, "timeframe": "15m"},
    "trend":            {"enabled": true, "weight": 0.35, "timeframe": "1d"},
    "intraday_momentum": {"enabled": true, "weight": 0.25, "timeframe": "30m"},
    "news_veto":        {"enabled": true}
  }',
  now() - interval '3 days'
);

-- Operaciones: dos cerradas (una ganadora y una perdedora) y una abierta.
insert into public.trades (
  bot_id, symbol, side, quantity, entry_price, exit_price, stop_loss, take_profit,
  fees, pnl, rationale, opened_at, closed_at
) values
(
  '33333333-3333-3333-3333-333333333333', 'BTCUSDT', 'buy', 0.02,
  68000, 69500, 67000, 69500, 2.75, 27.25,
  '{
    "signals": [
      {"agent": "kronos", "direction": "long", "confidence": 0.64},
      {"agent": "trend", "direction": "long", "confidence": 0.71},
      {"agent": "intraday_momentum", "direction": "flat", "confidence": 0.10}
    ],
    "weights": {"kronos": 0.40, "trend": 0.35, "intraday_momentum": 0.25},
    "simulation": {"p_target_before_stop": 0.58, "expected_value": 0.0042, "paths": 10100},
    "risk": {"verdict": "PASS", "position_size": 0.02, "reason": "dentro de límites"}
  }',
  now() - interval '3 days', now() - interval '2 days 20 hours'
),
(
  '33333333-3333-3333-3333-333333333333', 'ETHUSDT', 'buy', 0.5,
  3500, 3440, 3440, 3620, 1.74, -31.74,
  '{
    "signals": [
      {"agent": "kronos", "direction": "long", "confidence": 0.55},
      {"agent": "trend", "direction": "long", "confidence": 0.60},
      {"agent": "intraday_momentum", "direction": "long", "confidence": 0.52}
    ],
    "weights": {"kronos": 0.40, "trend": 0.35, "intraday_momentum": 0.25},
    "simulation": {"p_target_before_stop": 0.54, "expected_value": 0.0021, "paths": 10100},
    "risk": {"verdict": "PASS", "position_size": 0.5, "reason": "dentro de límites"}
  }',
  now() - interval '2 days', now() - interval '1 day 22 hours'
),
(
  '33333333-3333-3333-3333-333333333333', 'BTCUSDT', 'buy', 0.015,
  69200, null, 68200, 70800, 1.04, null,
  '{
    "signals": [
      {"agent": "kronos", "direction": "long", "confidence": 0.61},
      {"agent": "trend", "direction": "long", "confidence": 0.68},
      {"agent": "intraday_momentum", "direction": "long", "confidence": 0.57}
    ],
    "weights": {"kronos": 0.40, "trend": 0.35, "intraday_momentum": 0.25},
    "simulation": {"p_target_before_stop": 0.60, "expected_value": 0.0051, "paths": 10100},
    "risk": {"verdict": "PASS", "position_size": 0.015, "reason": "dentro de límites"}
  }',
  now() - interval '6 hours', null
);

-- Curva de equity: una foto por día durante los últimos 4 días.
insert into public.equity_snapshots (bot_id, ts, equity, cash, positions_value, drawdown) values
  ('33333333-3333-3333-3333-333333333333', date_trunc('hour', now()) - interval '3 days', 10000.00, 10000.00, 0, 0),
  ('33333333-3333-3333-3333-333333333333', date_trunc('hour', now()) - interval '2 days', 10027.25, 10027.25, 0, 0),
  ('33333333-3333-3333-3333-333333333333', date_trunc('hour', now()) - interval '1 day', 9995.51, 9995.51, 0, 0.0032),
  ('33333333-3333-3333-3333-333333333333', date_trunc('hour', now()), 10010.30, 8971.29, 1039.01, 0);

-- Un backtest terminado con su reporte.
insert into public.backtests (profile_id, status, config, report, started_at, finished_at)
values (
  '22222222-2222-2222-2222-222222222222',
  'done',
  '{"strategy": "trend", "symbols": ["BTCUSDT", "ETHUSDT"], "mode": "walk_forward",
    "in_sample_months": 12, "out_of_sample_months": 3, "start": "2020-01-01", "end": "2024-12-31"}',
  '{"sharpe_oos": 0.82, "max_drawdown": 0.19, "mc_p95_drawdown": 0.24, "trades": 143, "approved": true}',
  now() - interval '1 day 1 hour',
  now() - interval '1 day'
);
