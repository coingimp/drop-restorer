#!/usr/bin/env bash
set -Eeuo pipefail

PORT=8780
while (($#)); do
    case "$1" in
        --port)
            [[ $# -ge 2 ]] || { printf '%s\n' '--port требует числа.' >&2; exit 2; }
            PORT="$2"; shift 2
            ;;
        -h|--help)
            printf 'Использование: %s [--port 8780]\n' "$0"
            exit 0
            ;;
        *)
            printf 'Неизвестный параметр: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done
UNIT_NAME="drop-restorer-${PORT}.service"
if command -v systemctl >/dev/null 2>&1 && systemctl --user --version >/dev/null 2>&1; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    systemctl --user daemon-reload || true
fi
UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT_NAME"
rm -f -- "$UNIT_PATH"
printf 'Автозапуск %s отключён. Данные сборок не удалялись.\n' "$UNIT_NAME"
