#!/usr/bin/env bash
# `make dev-status`: qué está arriba del stack local.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

check() {
  if http_ok "$2"; then ok "$1: $2"; else warn "$1: sin respuesta ($2)"; fi
}
step "Stack local"
check "API" "$API_URL/api/health"
check "Web" "http://127.0.0.1:$WEB_PORT/"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null | sed 's/^/    /'
fi
step "Motor (/api/engine/status)"
curl -fsS --max-time 5 "$API_URL/api/engine/status" 2>/dev/null \
  | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);console.log(`    online: ${j.online}`);for(const c of j.components||[])console.log(`    ${c.component}: ${c.online?"en línea":"desconectado"} (hace ${c.age_seconds}s, modo ${(c.details&&c.details.mode)||"-"})`)})' \
  || warn "la API no responde"
