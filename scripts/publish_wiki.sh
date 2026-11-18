#!/usr/bin/env bash
# publish_wiki.sh — sincroniza docs/wiki/ con el repo de Wiki de GitHub.
#
# Uso:
#   scripts/publish_wiki.sh                       # usa CarloSantoro21/trading_maxxing
#   scripts/publish_wiki.sh otro-owner/otro-repo  # con argumento
#   OWNER_REPO=otro/repo scripts/publish_wiki.sh  # con variable de entorno
#
# Pre-requisito (una sola vez): el repo de Wiki no existe hasta que se crea la
# primera página desde la interfaz de GitHub. Si el clone falla, el script
# imprime instrucciones y sale con código 2.

set -euo pipefail

# --- Configuración ------------------------------------------------------------

DEFAULT_OWNER_REPO="CarloSantoro21/trading_maxxing"
OWNER_REPO="${1:-${OWNER_REPO:-$DEFAULT_OWNER_REPO}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WIKI_SRC="$REPO_ROOT/docs/wiki"

if [[ ! -d "$WIKI_SRC" ]]; then
  echo "error: no existe $WIKI_SRC" >&2
  exit 1
fi

WIKI_URL="https://github.com/${OWNER_REPO}.wiki.git"
COMMIT_MESSAGE="docs(wiki): sync from docs/wiki/"

TMP_DIR="$(mktemp -d -t trading-maxxing-wiki.XXXXXX)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# --- Clonar el repo de Wiki ---------------------------------------------------

echo "==> Clonando $WIKI_URL"
if ! git clone --depth 1 "$WIKI_URL" "$TMP_DIR/wiki" 2>"$TMP_DIR/clone.err"; then
  cat "$TMP_DIR/clone.err" >&2
  cat >&2 <<EOF

No se pudo clonar el repo de Wiki ($WIKI_URL).

Posibles causas:
  1. La Wiki aún no se ha inicializado. En GitHub, ve a:
       https://github.com/${OWNER_REPO}/wiki
     y crea la primera página desde la interfaz (puede ser "Home" vacía).
     Eso inicializa el repo .wiki.git. Luego vuelve a correr este script.
  2. No tienes permisos de escritura sobre ${OWNER_REPO}.
  3. Tu git no está autenticado para GitHub (usa gh auth login o un SSH key).

EOF
  exit 2
fi

WIKI_DIR="$TMP_DIR/wiki"

# --- Sincronizar archivos -----------------------------------------------------

echo "==> Copiando contenido de $WIKI_SRC al repo de Wiki"
# Copia todos los .md del nivel raíz de docs/wiki
find "$WIKI_SRC" -maxdepth 1 -type f -name '*.md' -exec cp -f {} "$WIKI_DIR/" \;

# Copia la carpeta img/ preservando estructura
if [[ -d "$WIKI_SRC/img" ]]; then
  mkdir -p "$WIKI_DIR/img"
  cp -Rf "$WIKI_SRC/img/." "$WIKI_DIR/img/"
fi

# --- Commit y push ------------------------------------------------------------

cd "$WIKI_DIR"

git add -A

if git diff --cached --quiet; then
  echo "==> No hay cambios en la Wiki; nada que publicar."
  exit 0
fi

# Permite correr el script en CI sin configurar git user.*
if ! git config user.email >/dev/null 2>&1; then
  git config user.email "wiki-sync@trading-maxxing.local"
fi
if ! git config user.name >/dev/null 2>&1; then
  git config user.name "trading_maxxing wiki sync"
fi

echo "==> Commit: $COMMIT_MESSAGE"
git commit -m "$COMMIT_MESSAGE"

echo "==> Push a origin"
git push origin HEAD

echo "==> Listo. Revisa la Wiki en: https://github.com/${OWNER_REPO}/wiki"
