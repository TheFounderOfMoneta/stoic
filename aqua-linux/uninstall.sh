#!/usr/bin/env bash
# Удаление ярлыков Aqua Linux. Настройки и история удаляются только с флагом --purge.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP=aqua-linux
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

"$HOME/.local/bin/$APP" quit 2>/dev/null || true
rm -f "$HOME/.local/bin/$APP" "$DATA/applications/$APP.desktop" "$CONF/autostart/$APP.desktop" \
      "$DATA/icons/hicolor/256x256/apps/$APP.png"
echo "Ярлыки и автозапуск удалены."
if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$CONF/$APP" "$DATA/$APP" "${XDG_CACHE_HOME:-$HOME/.cache}/$APP" "${XDG_STATE_HOME:-$HOME/.local/state}/$APP" "$ROOT/.venv"
    echo "Настройки, история, ссылки на модель и окружение .venv удалены (сами веса GigaAM не трогались)."
else
    echo "Настройки и история сохранены. Полное удаление: bash uninstall.sh --purge"
fi
