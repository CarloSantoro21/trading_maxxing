-- Tablas nuevas para el motor que corre en la Mac (no existían en Supabase).
--
-- El Worker no puede escribir en el Redis de la Mac, así que lo que antes eran streams
-- (tm:commands) ahora es una cola en D1 que el motor consulta por /internal/commands.
-- Los backtests ya eran una tabla: el motor lee los que están en `queued`.

-- Ajustes globales (key/value; value es JSON). Los cambia un admin.
CREATE TABLE settings (
  key         TEXT PRIMARY KEY CHECK (key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
  value       TEXT NOT NULL CHECK (json_valid(value)),
  updated_by  TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
-- Las dos integraciones con APIs externas de pago (noticias y explainer LLM) arrancan apagadas.
INSERT INTO settings (key, value) VALUES ('news_enabled', 'false'), ('explainer_enabled', 'false');

-- Último latido de cada componente del motor (agents, engine, forecast, ...).
CREATE TABLE engine_heartbeats (
  component     TEXT PRIMARY KEY
    CHECK (length(component) BETWEEN 1 AND 64 AND component NOT GLOB '*[^a-z0-9_-]*'),
  status        TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'degraded', 'error', 'stopping')),
  version       TEXT,
  details       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details) AND json_type(details) = 'object'),
  host          TEXT,
  started_at    TEXT,
  last_seen_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Cola de comandos de bots (start, stop, kill). Reemplaza al stream tm:commands.
-- seq da el orden; el motor confirma cada comando con /internal/commands/{id}/ack.
CREATE TABLE commands (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  type        TEXT NOT NULL CHECK (type IN ('start', 'stop', 'kill')),
  bot_id      TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  payload     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload) AND json_type(payload) = 'object'),
  issued_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  acked_at    TEXT,
  ack_status  TEXT CHECK (ack_status IS NULL OR ack_status IN ('done', 'failed')),
  ack_error   TEXT
);
CREATE INDEX commands_pending_idx ON commands (seq) WHERE acked_at IS NULL;

-- Decisiones del orquestador (auditoría; el flujo en vivo sigue en el Redis de la Mac).
CREATE TABLE decisions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id      TEXT REFERENCES bots (id) ON DELETE CASCADE,
  strategy    TEXT,
  symbol      TEXT,
  kind        TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 64),
  payload     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload) AND json_type(payload) = 'object'),
  decided_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX decisions_bot_decided_idx ON decisions (bot_id, decided_at DESC);

-- Estado libre del orquestador (key/value JSON) para sobrevivir reinicios de la Mac.
CREATE TABLE orchestrator_state (
  key         TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 128),
  value       TEXT NOT NULL CHECK (json_valid(value)),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
