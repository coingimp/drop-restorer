#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${DROP_RESTORER_VENV:-$ROOT/.venv-drop-restorer}/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    printf 'Окружение не найдено. Сначала выполните %s/install.sh\n' "$ROOT" >&2
    exit 1
fi

export DROP_RESTORER_WORKSPACE="$ROOT"
exec "$PYTHON_BIN" -X utf8 -m drop_restorer.web.launcher "$@"
