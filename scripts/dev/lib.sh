# Funciones comunes de scripts/dev/*.sh (stack local de `make dev`). Se carga con `source`.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEV_DIR="$ROOT/.dev"
LOG_DIR="$DEV_DIR/logs"
PID_DIR="$DEV_DIR/pids"
API_PORT="${API_PORT:-8787}"
WEB_PORT="${WEB_PORT:-3000}"
API_URL="http://127.0.0.1:${API_PORT}"
WEB_URL="http://localhost:${WEB_PORT}"
REDIS_PORT="${REDIS_PORT:-6379}"
SERVICE_TOKEN_DEFAULT="dev-service-token-cambiame"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_B=""; C_0=""
fi

step() { printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok() { printf '    %s✓%s %s\n' "$C_OK" "$C_0" "$*"; }
warn() { printf '    %s!%s %s\n' "$C_WARN" "$C_0" "$*"; }
die() { printf '%sError:%s %s\n' "$C_ERR" "$C_0" "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "falta '$1' en el PATH. $2"
}

# http_ok URL: true si responde 2xx.
http_ok() {
  curl -fsS -o /dev/null --max-time 5 "$1" 2>/dev/null
}

# wait_http URL SEGUNDOS NOMBRE [LOG]: espera a que la URL responda 2xx.
wait_http() {
  local url="$1" secs="$2" name="$3" log="${4:-}" i=0
  while ! http_ok "$url"; do
    i=$((i + 1))
    if [ "$i" -ge "$secs" ]; then
      [ -n "$log" ] && [ -f "$log" ] && { echo "--- últimas líneas de $log ---"; tail -n 40 "$log"; }
      die "$name no respondió en ${secs}s ($url)"
    fi
    sleep 1
  done
}

# pid_alive NOMBRE: true si el proceso guardado en .dev/pids/NOMBRE.pid sigue vivo.
pid_alive() {
  local f="$PID_DIR/$1.pid"
  [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null
}

# kill_tree PID: termina el proceso y todos sus descendientes (TERM y luego KILL).
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

# stop_pid NOMBRE: mata el árbol del proceso de .dev/pids/NOMBRE.pid y borra el archivo.
stop_pid() {
  local f="$PID_DIR/$1.pid" pid
  [ -f "$f" ] || return 0
  pid="$(cat "$f")"
  if kill -0 "$pid" 2>/dev/null; then
    kill_tree "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$f"
}

# listener_pids PUERTO: pids que escuchan en el puerto (TCP).
listener_pids() {
  lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

# ensure_docker [SEGUNDOS]: arranca Docker Desktop si hace falta y espera a `docker info`.
ensure_docker() {
  local secs="${1:-150}" i=0
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 && return 0
  if [ "$(uname)" = "Darwin" ]; then
    warn "Docker no responde: abriendo Docker Desktop (hasta ${secs}s)…"
    open -a Docker >/dev/null 2>&1 || return 1
  fi
  until docker info >/dev/null 2>&1; do
    i=$((i + 2))
    [ "$i" -ge "$secs" ] && return 1
    sleep 2
  done
}

# service_token: el SERVICE_TOKEN de apps/api/.dev.vars (el mismo que usa el motor).
service_token() {
  local f="$ROOT/apps/api/.dev.vars" t=""
  [ -f "$f" ] && t="$(sed -n 's/^SERVICE_TOKEN=//p' "$f" | tail -1)"
  echo "${t:-$SERVICE_TOKEN_DEFAULT}"
}

# engine_online: true si /api/engine/status dice online (agents y engine con latido fresco).
engine_online() {
  curl -fsS --max-time 5 "$API_URL/api/engine/status" 2>/dev/null \
    | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s);const c=new Set((j.components||[]).filter(x=>x.online).map(x=>x.component));process.exit(j.online&&c.has("agents")&&c.has("engine")?0:1)}catch{process.exit(1)}})'
}
