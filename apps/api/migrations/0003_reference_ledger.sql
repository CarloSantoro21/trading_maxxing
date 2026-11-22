-- Ledger de referencia de cada estrategia (sin capital real), portado de
-- 20261109000200_reference_ledger.sql. Solo el motor lo escribe (/internal/reference/*);
-- cualquier usuario lo lee. shadow_days_clean ya no es una columna calculada: la API la
-- calcula al responder (ver src/lib/strategies.ts).

CREATE TABLE reference_trades (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id  TEXT NOT NULL REFERENCES strategies (id) ON DELETE CASCADE,
  started_at   TEXT NOT NULL,
  state        TEXT NOT NULL CHECK (state IN ('shadow', 'active', 'probation')),
  symbol       TEXT NOT NULL CHECK (symbol IN ('BTCUSDT', 'ETHUSDT')),
  quantity     REAL NOT NULL CHECK (quantity > 0),
  entry_price  REAL NOT NULL CHECK (entry_price > 0),
  exit_price   REAL NOT NULL CHECK (exit_price > 0),
  fees         REAL NOT NULL DEFAULT 0 CHECK (fees >= 0),
  pnl          REAL NOT NULL,
  ret          REAL NOT NULL CHECK (ret >= -1),
  opened_at    TEXT NOT NULL,
  closed_at    TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK (closed_at >= opened_at),
  CHECK (opened_at >= started_at),
  UNIQUE (strategy_id, symbol, closed_at)
);
CREATE INDEX reference_trades_strategy_closed_idx ON reference_trades (strategy_id, closed_at DESC);

CREATE TABLE reference_equity (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id      TEXT NOT NULL REFERENCES strategies (id) ON DELETE CASCADE,
  started_at       TEXT NOT NULL,
  day              TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  state            TEXT NOT NULL CHECK (state IN ('shadow', 'active', 'probation')),
  equity           REAL NOT NULL CHECK (equity >= 0),
  cash             REAL NOT NULL CHECK (cash >= 0),
  positions_value  REAL NOT NULL DEFAULT 0 CHECK (positions_value >= 0),
  drawdown         REAL NOT NULL DEFAULT 0 CHECK (drawdown >= 0 AND drawdown <= 1),
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK (day >= substr(started_at, 1, 10)),
  UNIQUE (strategy_id, day)
);
CREATE INDEX reference_equity_strategy_day_idx ON reference_equity (strategy_id, day);
