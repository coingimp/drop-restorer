#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="${DROP_RESTORER_VENV:-$ROOT/.venv-drop-restorer}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'Не найден %s. Установите Python 3.11 или новее и повторите запуск.\n' "$PYTHON_BIN" >&2
    exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf 'DropRestorer требует Python 3.11 или новее.\n' >&2
    exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    printf 'Создаю окружение %s\n' "$VENV"
    "$PYTHON_BIN" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --editable "$ROOT"

printf '\nГотово. Откройте панель командой:\n  %s/start-drop-restorer.sh\n' "$ROOT"
printf 'Для фонового запуска без браузера: %s/start-drop-restorer.sh --no-browser\n' "$ROOT"
