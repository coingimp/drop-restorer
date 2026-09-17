#!/usr/bin/env bash
set -Eeuo pipefail

PORT=8780
LINGER=0
while (($#)); do
    case "$1" in
        --port)
            [[ $# -ge 2 ]] || { printf '%s\n' '--port требует числа.' >&2; exit 2; }
            PORT="$2"; shift 2
            ;;
        --linger)
            LINGER=1; shift
            ;;
        -h|--help)
            printf 'Использование: %s [--port 8780] [--linger]\n' "$0"
            exit 0
            ;;
        *)
            printf 'Неизвестный параметр: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1024 || PORT > 65535)); then
    printf 'Порт должен быть целым числом от 1024 до 65535.\n' >&2
    exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${DROP_RESTORER_VENV:-$ROOT/.venv-drop-restorer}/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    printf 'Окружение не найдено. Сначала выполните %s/install.sh\n' "$ROOT" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user --version >/dev/null 2>&1; then
    printf 'Не найден пользовательский systemd. Запускайте start-drop-restorer.sh вручную.\n' >&2
    exit 1
fi
if ! command -v systemd-escape >/dev/null 2>&1; then
    printf 'Не найден systemd-escape; не могу безопасно записать путь с пробелами.\n' >&2
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_NAME="drop-restorer-${PORT}.service"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
mkdir -p "$UNIT_DIR"
ESCAPED_ROOT="$(systemd-escape --path "$ROOT")"
ESCAPED_PYTHON="$(systemd-escape --path "$PYTHON_BIN")"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=DropRestorer local panel (port $PORT)
After=default.target

[Service]
Type=simple
WorkingDirectory=$ESCAPED_ROOT
Environment=DROP_RESTORER_WORKSPACE=$ESCAPED_ROOT
Environment=QT_QPA_PLATFORM=offscreen
Environment=QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu
ExecStart=$ESCAPED_PYTHON -X utf8 -u -m drop_restorer.web --port $PORT --background
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=20

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
if ((LINGER)); then
    if command -v loginctl >/dev/null 2>&1; then
        loginctl enable-linger "$USER" || printf 'Не удалось включить linger; сервис будет работать после входа пользователя.\n' >&2
    else
        printf 'loginctl не найден; linger не включён.\n' >&2
    fi
fi

printf 'Сервис %s включён: http://127.0.0.1:%s/\n' "$UNIT_NAME" "$PORT"
printf 'Статус: systemctl --user status %s\n' "$UNIT_NAME"
