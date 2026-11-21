-- Ciclo de vida de las estrategias, portado de 20261108000100_strategies.sql.
--
-- Igual que en Postgres, el estado solo cambia insertando en strategy_transitions: el
-- trigger BEFORE INSERT rechaza la transición si from_state ya no es el estado actual
-- (la API lo traduce a 409 "stale_state") y el AFTER INSERT actualiza strategies.state.
-- Las reglas de qué transición se permite están en el CHECK (y también en la API).

CREATE TABLE strategies (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL UNIQUE
    CHECK (length(name) BETWEEN 1 AND 64 AND name GLOB '[a-z]*' AND name NOT GLOB '*[^a-z0-9_]*'),
  state             TEXT NOT NULL DEFAULT 'research'
    CHECK (state IN ('research', 'lab', 'shadow', 'active', 'probation', 'retired')),
  params            TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(params) AND json_type(params) = 'object'),
  lab_report        TEXT
    CHECK (lab_report IS NULL OR (json_valid(lab_report) AND json_type(lab_report) = 'object')),
  live_metrics      TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(live_metrics) AND json_type(live_metrics) = 'object'),
  state_changed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX strategies_state_idx ON strategies (state);

-- Historial de solo inserción. actor es 'system' o el id (uuid) de la persona.
CREATE TABLE strategy_transitions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id  TEXT NOT NULL REFERENCES strategies (id) ON DELETE RESTRICT,
  from_state   TEXT NOT NULL
    CHECK (from_state IN ('research', 'lab', 'shadow', 'active', 'probation', 'retired')),
  to_state     TEXT NOT NULL
    CHECK (to_state IN ('research', 'lab', 'shadow', 'active', 'probation', 'retired')),
  actor        TEXT NOT NULL
    CHECK (actor = 'system' OR (length(actor) = 36 AND actor NOT GLOB '*[^0-9a-f-]*')),
  reason       TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK ((from_state || '>' || to_state) IN (
    'research>lab', 'research>retired',
    'lab>shadow', 'lab>research', 'lab>retired',
    'shadow>active', 'shadow>lab', 'shadow>retired',
    'active>probation', 'active>retired',
    'probation>active', 'probation>retired',
    'retired>lab'
  )),
  -- Promover a active exige una persona.
  CHECK (to_state <> 'active' OR actor <> 'system')
);
CREATE INDEX strategy_transitions_strategy_id_created_at_idx
  ON strategy_transitions (strategy_id, created_at, id);

CREATE TRIGGER strategy_transitions_guard_state
BEFORE INSERT ON strategy_transitions
FOR EACH ROW
WHEN NEW.from_state IS NOT (SELECT state FROM strategies WHERE id = NEW.strategy_id)
BEGIN
  SELECT RAISE(ABORT, 'stale_state: la estrategia ya no está en el estado de origen');
END;

CREATE TRIGGER strategy_transitions_apply
AFTER INSERT ON strategy_transitions
FOR EACH ROW
BEGIN
  UPDATE strategies
  SET state = NEW.to_state,
      state_changed_at = NEW.created_at,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  WHERE id = NEW.strategy_id;
END;
