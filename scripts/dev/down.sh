#!/usr/bin/env bash
# `make dev-down`: apaga lo que levantó `make dev` (API, web y motor). Conserva la D1 local y
# el volumen de Redis; `make dev-reset` los borra.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

step "Apagando el stack local"
for name in web api agents engine redis; do
  if [ -f "$PID_DIR/$name.pid" ]; then
    stop_pid "$name"
    ok "$name detenido"
  fi
done
# Restos de una corrida anterior (p. ej. un proceso huérfano de wrangler o de next dev).
for port in "$API_PORT" "$WEB_PORT"; do
  for pid in $(listener_pids "$port"); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    case "$cmd" in
      *"$ROOT"*|*workerd*|*wrangler*|*next-server*|*"next dev"*)
        kill_tree "$pid"
        ok "proceso en :$port detenido ($pid)"
        ;;
    esac
  done
done

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  COMPOSE_PROFILES=forecast docker compose down --remove-orphans >/dev/null 2>&1 \
    && ok "contenedores del motor detenidos"
fi
rm -f "$DEV_DIR/motor"
ok "listo"
