-- Datos de prueba para desarrollo local (D1 local de `wrangler dev --local`).
-- Se cargan con `make dev` (o pnpm db:seed:local después de pnpm db:migrate:local).
-- Nunca se cargan en las bases de la nube (tm-dev ni tm-prod): ahí solo corren las migraciones.
--
-- Usuario demo: demo@trading-maxxing.local, admin. En local AUTH_MODE=dev entra con él
-- (DEV_USER_EMAIL); no hay contraseña porque el login real lo hace Cloudflare Access.

INSERT INTO users (id, email, role) VALUES
  ('11111111-1111-1111-1111-111111111111', 'demo@trading-maxxing.local', 'admin');

-- Perfil: riesgo 3 (moderado, drawdown máximo 20 %) y 10,000 USDT.
INSERT INTO profiles (id, user_id, risk_level, capital, max_drawdown)
VALUES ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 3, 10000, 0.20);

-- Meta: 12 % en un año.
INSERT INTO goals (id, profile_id, target_return, deadline)
VALUES ('55555555-5555-5555-5555-555555555501', '22222222-2222-2222-2222-222222222222', 0.12,
        date('now', '+1 year'));

-- Bot con las estrategias del MVP. Empieza DETENIDO: en la demo local (`make dev`) se arranca
-- desde el dashboard con "Arrancar" y entonces opera la estrategia demo_pulse (al final).
INSERT INTO bots (id, profile_id, name, status, strategy_config, started_at, stopped_at)
VALUES (
  '33333333-3333-3333-3333-333333333333',
  '22222222-2222-2222-2222-222222222222',
  'Comité BTC/ETH',
  'stopped',
  '{"kronos":{"enabled":true,"weight":0.40,"timeframe":"15m"},"trend":{"enabled":true,"weight":0.35,"timeframe":"1d"},"intraday_momentum":{"enabled":true,"weight":0.25,"timeframe":"30m"},"news_veto":{"enabled":true}}',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-3 days'),
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 hours')
);

-- Operaciones: dos cerradas (una ganadora y una perdedora) y una abierta.
INSERT INTO trades (id, bot_id, symbol, side, quantity, entry_price, exit_price, stop_loss,
                    take_profit, fees, pnl, rationale, opened_at, closed_at) VALUES
(
  '66666666-6666-6666-6666-666666666601', '33333333-3333-3333-3333-333333333333', 'BTCUSDT', 'buy',
  0.02, 68000, 69500, 67000, 69500, 2.75, 27.25,
  '{"sleeve":"trend","signals":[{"agent":"kronos","direction":"long","confidence":0.64},{"agent":"trend","direction":"long","confidence":0.71},{"agent":"intraday_momentum","direction":"flat","confidence":0.10}],"weights":{"kronos":0.40,"trend":0.35,"intraday_momentum":0.25},"simulation":{"p_target_before_stop":0.58,"expected_value":0.0042,"paths":10100},"risk":{"verdict":"PASS","position_size":0.02,"reason":"dentro de límites"}}',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-3 days'),
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 days', '-20 hours')
),
(
  '66666666-6666-6666-6666-666666666602', '33333333-3333-3333-3333-333333333333', 'ETHUSDT', 'buy',
  0.5, 3500, 3440, 3440, 3620, 1.74, -31.74,
  '{"sleeve":"trend","signals":[{"agent":"kronos","direction":"long","confidence":0.55},{"agent":"trend","direction":"long","confidence":0.60},{"agent":"intraday_momentum","direction":"long","confidence":0.52}],"weights":{"kronos":0.40,"trend":0.35,"intraday_momentum":0.25},"simulation":{"p_target_before_stop":0.54,"expected_value":0.0021,"paths":10100},"risk":{"verdict":"PASS","position_size":0.5,"reason":"dentro de límites"}}',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 days'),
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 days', '-22 hours')
),
(
  '66666666-6666-6666-6666-666666666603', '33333333-3333-3333-3333-333333333333', 'BTCUSDT', 'buy',
  0.015, 69200, NULL, 68200, 70800, 1.04, NULL,
  '{"sleeve":"trend","signals":[{"agent":"kronos","direction":"long","confidence":0.61},{"agent":"trend","direction":"long","confidence":0.68},{"agent":"intraday_momentum","direction":"long","confidence":0.57}],"weights":{"kronos":0.40,"trend":0.35,"intraday_momentum":0.25},"simulation":{"p_target_before_stop":0.60,"expected_value":0.0051,"paths":10100},"risk":{"verdict":"PASS","position_size":0.015,"reason":"dentro de límites"}}',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-6 hours'),
  NULL
);

-- Curva de equity: una foto por día durante los últimos 4 días.
INSERT INTO equity_snapshots (bot_id, ts, equity, cash, positions_value, drawdown) VALUES
  ('33333333-3333-3333-3333-333333333333', strftime('%Y-%m-%dT%H:00:00.000Z', 'now', '-3 days'), 10000.00, 10000.00, 0, 0),
  ('33333333-3333-3333-3333-333333333333', strftime('%Y-%m-%dT%H:00:00.000Z', 'now', '-2 days'), 10027.25, 10027.25, 0, 0),
  ('33333333-3333-3333-3333-333333333333', strftime('%Y-%m-%dT%H:00:00.000Z', 'now', '-1 days'), 9995.51, 9995.51, 0, 0.0032),
  ('33333333-3333-3333-3333-333333333333', strftime('%Y-%m-%dT%H:00:00.000Z', 'now'), 10010.30, 8971.29, 1039.01, 0);

-- Un backtest terminado con su reporte.
INSERT INTO backtests (id, profile_id, status, config, report, started_at, finished_at)
VALUES (
  '77777777-7777-7777-7777-777777777701',
  '22222222-2222-2222-2222-222222222222',
  'done',
  '{"strategy":"trend","symbols":["BTCUSDT","ETHUSDT"],"mode":"walk_forward","in_sample_months":12,"out_of_sample_months":3,"start":"2020-01-01","end":"2024-12-31"}',
  '{"sharpe_oos":0.82,"max_drawdown":0.19,"mc_p95_drawdown":0.24,"trades":143,"approved":true}',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 days', '-1 hours'),
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 days')
);

-- Estrategias de la biblioteca. El estado solo cambia insertando en strategy_transitions
-- (los triggers lo aplican): trend llega a active (aprobada por el usuario demo), kronos se
-- queda en shadow e intraday_momentum en el laboratorio.
INSERT INTO strategies (id, name, params, created_at, state_changed_at) VALUES
  ('44444444-4444-4444-4444-444444444401', 'trend', '{"lookbacks":[20,50,100],"timeframe":"1d"}',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days')),
  ('44444444-4444-4444-4444-444444444402', 'kronos', '{"model":"Kronos-small","timeframe":"4h","samples":20}',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days')),
  ('44444444-4444-4444-4444-444444444403', 'intraday_momentum', '{"timeframe":"30m"}',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-20 days'));

INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444401', 'research', 'lab', 'system', 'entra al laboratorio', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-19 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444401', 'lab', 'shadow', 'system', 'pasa los criterios del laboratorio', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-18 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444401', 'shadow', 'active', '11111111-1111-1111-1111-111111111111', '7 días en shadow sin fallas', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-10 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444402', 'research', 'lab', 'system', 'entra al laboratorio', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-12 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444402', 'lab', 'shadow', 'system', 'pasa los criterios del laboratorio', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-5 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444403', 'research', 'lab', 'system', 'entra al laboratorio', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 days'));

UPDATE strategies
SET lab_report = '{"sharpe_oos":0.82,"max_drawdown_p95":0.24,"max_drawdown_p99":0.31,"trades":143,"approved":true}',
    live_metrics = '{"drawdown":0.03,"sharpe_last_30":0.9,"trades":12}'
WHERE name = 'trend';

UPDATE strategies
SET lab_report = '{"sharpe_oos":0.66,"max_drawdown_p95":0.21,"max_drawdown_p99":0.28,"trades":118,"approved":true}'
WHERE name = 'kronos';

-- ---------------------------------------------------------------------------------------
-- Estrategia SOLO DE DEMO: demo_pulse (services/agents/src/tm_agents/strategies/demo_pulse.py).
-- Alterna long/flat en BTCUSDT cada TM_DEMO_PULSE_MINUTES (2) minutos sobre velas de 1 min para
-- ver el flujo completo en local: señal, SimulationGate, bot, engine, trades y dashboard.
-- Solo tiene código si el worker agents corre con TM_DEMO_STRATEGY=1 (lo pone `make dev`).
-- Esta fila vive únicamente en este seed local: ninguna migración la crea, así que nunca
-- existe en tm-dev ni en tm-prod. Su lab_report es inventado (solo para que el allocator le
-- dé capital): no es evidencia de nada.
-- ---------------------------------------------------------------------------------------
INSERT INTO strategies (id, name, params, created_at, state_changed_at) VALUES
  ('44444444-4444-4444-4444-444444444499', 'demo_pulse', '{"symbols":["BTCUSDT"]}',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-9 days'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-9 days'));

INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444499', 'research', 'lab', 'system', 'demo local', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-9 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444499', 'lab', 'shadow', 'system', 'demo local', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-8 days'));
INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES
  ('44444444-4444-4444-4444-444444444499', 'shadow', 'active', '11111111-1111-1111-1111-111111111111', 'demo local: solo para ver el flujo', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 days'));

UPDATE strategies
SET lab_report = '{"demo":true,"note":"estrategia de demo local, sin evidencia","aggregate":{"volatility":0.4,"sharpe":1.0},"montecarlo":{"max_drawdown":{"p95":0.15,"p99":0.25}}}'
WHERE name = 'demo_pulse';
