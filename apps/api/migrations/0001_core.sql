-- Esquema base en D1 (SQLite), portado de supabase/migrations (20261107000000_enums,
-- 20261107000100_tables, 20261108000000_profiles_max_drawdown_open,
-- 20261109000000_app_roles y 20261109000100_profiles_risk_levels).
--
-- Reglas del port:
--   * enums de Postgres        -> CHECK (columna IN (...))
--   * uuid                     -> TEXT (la API los genera con crypto.randomUUID())
--   * timestamptz              -> TEXT ISO 8601 en UTC ("2026-11-21T10:00:00.000Z")
--   * numeric                  -> REAL
--   * jsonb / text[]           -> TEXT con JSON (json_valid / json_type en el CHECK)
--   * RLS                      -> filtros por user_id en cada query de la API
--   * Supabase Auth + app_roles -> tabla users (el login lo hace Cloudflare Access)
--   * triggers de updated_at y de max_drawdown -> los pone la API al escribir

-- Usuarios del equipo. Cloudflare Access autentica el correo; aquí vive el rol.
CREATE TABLE users (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 320 AND instr(email, '@') > 1)
);

-- Perfil de inversión (uno por usuario). max_drawdown lo deriva la API de risk_level
-- (1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40).
CREATE TABLE profiles (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
  horizon       TEXT NOT NULL DEFAULT 'medium' CHECK (horizon IN ('short', 'medium', 'long')),
  risk_level    INTEGER NOT NULL CHECK (risk_level BETWEEN 1 AND 5),
  capital       REAL NOT NULL CHECK (capital >= 500),
  max_drawdown  REAL NOT NULL CHECK (max_drawdown > 0 AND max_drawdown < 1),
  instruments   TEXT NOT NULL DEFAULT '["BTCUSDT","ETHUSDT"]'
    CHECK (json_valid(instruments) AND json_type(instruments) = 'array'
           AND json_array_length(instruments) >= 1),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE goals (
  id             TEXT PRIMARY KEY,
  profile_id     TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
  target_return  REAL NOT NULL CHECK (target_return > 0),
  deadline       TEXT NOT NULL CHECK (deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX goals_profile_id_idx ON goals (profile_id);

CREATE TABLE bots (
  id               TEXT PRIMARY KEY,
  profile_id       TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
  name             TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  status           TEXT NOT NULL DEFAULT 'stopped'
    CHECK (status IN ('stopped', 'running', 'paused', 'killed')),
  strategy_config  TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(strategy_config) AND json_type(strategy_config) = 'object'),
  started_at       TEXT,
  stopped_at       TEXT,
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX bots_profile_id_idx ON bots (profile_id);
CREATE INDEX bots_status_idx ON bots (status);

-- Operaciones de un bot. Solo el motor (/internal) las escribe; el usuario solo las lee.
CREATE TABLE trades (
  id           TEXT PRIMARY KEY,
  bot_id       TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
  symbol       TEXT NOT NULL CHECK (symbol IN ('BTCUSDT', 'ETHUSDT')),
  side         TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity     REAL NOT NULL CHECK (quantity > 0),
  entry_price  REAL NOT NULL CHECK (entry_price > 0),
  exit_price   REAL CHECK (exit_price IS NULL OR exit_price > 0),
  stop_loss    REAL CHECK (stop_loss IS NULL OR stop_loss > 0),
  take_profit  REAL CHECK (take_profit IS NULL OR take_profit > 0),
  fees         REAL NOT NULL DEFAULT 0 CHECK (fees >= 0),
  pnl          REAL,
  rationale    TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(rationale) AND json_type(rationale) = 'object'),
  opened_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  closed_at    TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK ((closed_at IS NULL) = (exit_price IS NULL)),
  CHECK (closed_at IS NULL OR closed_at >= opened_at),
  CHECK (stop_loss IS NULL
         OR (side = 'buy' AND stop_loss < entry_price)
         OR (side = 'sell' AND stop_loss > entry_price)),
  CHECK (take_profit IS NULL
         OR (side = 'buy' AND take_profit > entry_price)
         OR (side = 'sell' AND take_profit < entry_price))
);
CREATE INDEX trades_bot_id_opened_at_idx ON trades (bot_id, opened_at DESC);
CREATE INDEX trades_symbol_idx ON trades (symbol);
CREATE INDEX trades_open_idx ON trades (bot_id) WHERE closed_at IS NULL;

-- Foto periódica del equity de un bot. Solo el motor la escribe.
CREATE TABLE equity_snapshots (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id           TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
  ts               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  equity           REAL NOT NULL CHECK (equity >= 0),
  cash             REAL NOT NULL CHECK (cash >= 0),
  positions_value  REAL NOT NULL DEFAULT 0,
  drawdown         REAL NOT NULL DEFAULT 0 CHECK (drawdown >= 0 AND drawdown <= 1),
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  UNIQUE (bot_id, ts)
);
CREATE INDEX equity_snapshots_bot_ts_idx ON equity_snapshots (bot_id, ts DESC);

-- Backtests: el usuario los encola (queued); el motor los corre y escribe el reporte.
CREATE TABLE backtests (
  id           TEXT PRIMARY KEY,
  profile_id   TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'done', 'failed')),
  config       TEXT NOT NULL CHECK (json_valid(config) AND json_type(config) = 'object'),
  report       TEXT CHECK (report IS NULL OR (json_valid(report) AND json_type(report) = 'object')),
  error        TEXT,
  started_at   TEXT,
  finished_at  TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK (status <> 'done' OR report IS NOT NULL),
  CHECK (status <> 'failed' OR error IS NOT NULL),
  CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX backtests_profile_id_created_at_idx ON backtests (profile_id, created_at DESC);
CREATE INDEX backtests_status_idx ON backtests (status);
