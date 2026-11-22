#!/usr/bin/env bash
# `make dev`: levanta el sistema completo en local (docs/correr-local.md).
#
#   1. API:   wrangler dev (Worker + D1 local, AUTH_MODE=dev) en :8787, con migraciones y seed.
#   2. Web:   next dev en :3000 (reenvía /api/* a la API local, NEXT_PUBLIC_ENV=local).
#   3. Motor: docker compose (redis + agents + engine) con la estrategia demo_pulse y la
#             ejecución simulada si no hay llaves de Binance Testnet en .env.
#
# Variables: FORECAST=1 (también Kronos, perfil forecast), MOTOR=native (uv + redis-server
# sin Docker), API_PORT, WEB_PORT, TM_DEMO_PULSE_MINUTES.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

MOTOR="${MOTOR:-docker}"
need node "Instala Node 24 (ver .nvmrc)."
need pnpm "corepack enable (pnpm 9)."
need curl ""

# --- 0. Docker (o motor nativo) -------------------------------------------------------------
if [ "$MOTOR" != "native" ]; then
  step "Docker"
  if ensure_docker 150; then
    ok "docker responde ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'))"
  elif command -v redis-server >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
    warn "Docker no arrancó: el motor correrá nativo (uv + redis-server). Ver docs/correr-local.md."
    MOTOR=native
  else
    die "Docker no arrancó. Ábrelo a mano (open -a Docker) o instala redis (brew install redis) y usa MOTOR=native."
  fi
fi

# --- 1. API ----------------------------------------------------------------------------------
step "API (Cloudflare Worker con wrangler dev, D1 local)"
[ -d apps/api/node_modules ] || pnpm --dir apps/api install --frozen-lockfile
[ -f apps/api/.dev.vars ] || { cp apps/api/.dev.vars.example apps/api/.dev.vars; ok "apps/api/.dev.vars creado"; }
(cd apps/api && pnpm -s build:reports >/dev/null)
(cd apps/api && CI=1 pnpm exec wrangler d1 migrations apply DB --env dev --local >"$LOG_DIR/migrate.log" 2>&1) \
  || { tail -n 30 "$LOG_DIR/migrate.log"; die "fallaron las migraciones de la D1 local"; }
ok "migraciones aplicadas"
users="$(cd apps/api && pnpm exec wrangler d1 execute DB --env dev --local --json \
  --command 'SELECT count(*) AS n FROM users' 2>/dev/null \
  | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{console.log(JSON.parse(s)[0].results[0].n)}catch{console.log("?")}})')"
if [ "$users" = "0" ]; then
  (cd apps/api && pnpm exec wrangler d1 execute DB --env dev --local --file seed/seed.sql >"$LOG_DIR/seed.log" 2>&1) \
    || { tail -n 30 "$LOG_DIR/seed.log"; die "falló el seed de la D1 local"; }
  ok "seed cargado (demo@trading-maxxing.local, perfil, bot detenido y estrategia demo_pulse)"
else
  ok "la D1 local ya tiene datos ($users usuarios); el seed no se vuelve a cargar (make dev-reset lo borra)"
fi

if http_ok "$API_URL/api/health"; then
  ok "la API ya estaba corriendo en $API_URL"
else
  [ -z "$(listener_pids "$API_PORT")" ] || die "el puerto $API_PORT está ocupado por otro proceso (lsof -i :$API_PORT)"
  (cd apps/api && nohup pnpm exec wrangler dev --env dev --local --ip 0.0.0.0 --port "$API_PORT" \
    >"$LOG_DIR/api.log" 2>&1 & echo $! >"$PID_DIR/api.pid")
  wait_http "$API_URL/api/health" 120 "La API" "$LOG_DIR/api.log"
  ok "API en $API_URL (log: .dev/logs/api.log)"
fi

# --- 2. Web ----------------------------------------------------------------------------------
step "Web (Next.js dev contra la API local)"
[ -d apps/web/node_modules ] || pnpm --dir apps/web install --frozen-lockfile
if http_ok "http://127.0.0.1:$WEB_PORT/"; then
  ok "la web ya estaba corriendo en $WEB_URL"
else
  [ -z "$(listener_pids "$WEB_PORT")" ] || die "el puerto $WEB_PORT está ocupado por otro proceso (lsof -i :$WEB_PORT)"
  (cd apps/web && NEXT_PUBLIC_ENV=local NEXT_PUBLIC_COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)" \
    API_DEV_URL="$API_URL" NEXT_TELEMETRY_DISABLED=1 \
    nohup pnpm exec next dev --port "$WEB_PORT" >"$LOG_DIR/web.log" 2>&1 & echo $! >"$PID_DIR/web.pid")
  wait_http "http://127.0.0.1:$WEB_PORT/" 180 "La web" "$LOG_DIR/web.log"
  # next dev compila cada página la primera vez: se piden ya para que abran rápido.
  for page in /dashboard /onboarding /strategies /goals /backtests; do
    curl -fsS -o /dev/null --max-time 120 "http://127.0.0.1:$WEB_PORT$page" || true
  done
  ok "web en $WEB_URL (log: .dev/logs/web.log)"
fi

# --- 3. Motor --------------------------------------------------------------------------------
token="$(service_token)"
if [ "$MOTOR" = "native" ]; then
  step "Motor nativo (uv + redis-server, sin Docker)"
  need uv "brew install uv"
  need redis-server "brew install redis"
  if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    nohup redis-server --port "$REDIS_PORT" --save "" --appendonly no >"$LOG_DIR/redis.log" 2>&1 &
    echo $! >"$PID_DIR/redis.pid"
    sleep 1
  fi
  uv sync --all-packages --frozen >/dev/null
  export REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0" TM_API_URL="$API_URL" TM_SERVICE_TOKEN="$token" \
    TM_DATA_DIR="$ROOT/data/bars" TM_DEMO_STRATEGY=1 TM_DEMO_PULSE_MINUTES="${TM_DEMO_PULSE_MINUTES:-2}" \
    EQUITY_SNAPSHOT_SECONDS="${EQUITY_SNAPSHOT_SECONDS:-60}" COMMANDS_POLL_SECONDS=3 \
    ENGINE_EXECUTION="${ENGINE_EXECUTION:-auto}" ENGINE_SYMBOLS=BTCUSDT,ETHUSDT
  pid_alive agents || { HEARTBEAT_FILE="$DEV_DIR/agents.heartbeat" nohup uv run --frozen python -m tm_agents \
    >"$LOG_DIR/agents.log" 2>&1 & echo $! >"$PID_DIR/agents.pid"; }
  pid_alive engine || { HEARTBEAT_FILE="$DEV_DIR/engine.heartbeat" nohup uv run --frozen python -m tm_engine \
    >"$LOG_DIR/engine.log" 2>&1 & echo $! >"$PID_DIR/engine.pid"; }
  echo native >"$DEV_DIR/motor"
  ok "agents y engine nativos (logs: .dev/logs/agents.log y engine.log)"
else
  step "Motor (docker compose: redis + agents + engine${FORECAST:+ + forecast})"
  services=(redis agents engine)
  profiles=""
  if [ "${FORECAST:-}" = "1" ]; then services+=(forecast); profiles="forecast"; fi
  COMPOSE_PROFILES="$profiles" TM_DEMO_STRATEGY=1 TM_API_URL="http://host.docker.internal:$API_PORT" \
    TM_SERVICE_TOKEN="$token" docker compose up -d --build --wait "${services[@]}"
  echo docker >"$DEV_DIR/motor"
  ok "motor arriba (logs: make dev-logs)"
fi

step "Esperando el latido del motor en /api/engine/status"
for i in $(seq 1 90); do
  engine_online && break
  [ "$i" -eq 90 ] && die "el motor no reportó latido en 90 s (make dev-logs)"
  sleep 1
done
ok "motor en línea (agents + engine)"
mode="$(curl -fsS "$API_URL/api/engine/status" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);const e=(j.components||[]).find(c=>c.component==="engine");console.log((e&&e.details&&e.details.mode)||"?")})')"

cat <<EOF

${C_B}Listo. trading_maxxing corre en local.${C_0}

  Web        ${C_B}$WEB_URL${C_0}   (entras como demo@trading-maxxing.local, admin)
  API        $API_URL/api/health   (contrato: docs/arquitectura/api.md)
  Motor      ejecución: ${C_B}$mode${C_0}   estrategia de demo: demo_pulse (pulso de ${TM_DEMO_PULSE_MINUTES:-2} min en BTCUSDT)

  1. Abre $WEB_URL/dashboard y pulsa ${C_B}Arrancar${C_0} en el bot.
  2. En 2 a 4 minutos aparece la primera operación en "Últimas operaciones".
  3. Estrategias: $WEB_URL/strategies  ·  Metas: $WEB_URL/goals  ·  Admin: $WEB_URL/admin

  Logs: make dev-logs (motor), .dev/logs/api.log y .dev/logs/web.log
  Prueba automática: make e2e-local    Apagar: ${C_B}make dev-down${C_0}
EOF
