#!/usr/bin/env bash
# `make e2e-local`: Playwright contra el stack local REAL (apps/web/tests/e2e/local-stack.spec.ts).
#
# Si el stack no está arriba lo levanta con `make dev` y, al terminar, lo apaga (KEEP_UP=1 lo
# deja corriendo). Si ya estaba arriba lo usa y lo deja como estaba.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
cd "$ROOT"

started=0
if ! http_ok "$API_URL/api/health" || ! http_ok "http://127.0.0.1:$WEB_PORT/" || ! engine_online; then
  "$ROOT/scripts/dev/up.sh"
  started=1
fi

cleanup() {
  if [ "$started" = "1" ] && [ "${KEEP_UP:-0}" != "1" ]; then
    "$ROOT/scripts/dev/down.sh"
  fi
}
trap cleanup EXIT

step "Playwright contra el stack local ($WEB_URL)"
pnpm --dir apps/web exec playwright install chromium >/dev/null
cd apps/web
LOCAL_STACK=1 E2E_SKIP_WEBSERVER=1 E2E_BASE_URL="http://127.0.0.1:$WEB_PORT" LOCAL_API_URL="$API_URL" \
  pnpm exec playwright test local-stack.spec.ts --reporter=list
