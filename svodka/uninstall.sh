#!/usr/bin/env bash
# Удаление «Сводки»: таймер, ярлык, автозапуск, команда svodka, окружение Python.
# Ваши статьи, закладки и то, что лента о вас узнала, удаляются только если вы согласитесь.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$HOME/.local/bin/svodka"

if [[ -x "$LAUNCHER" ]]; then
  "$LAUNCHER" remove-timer
  "$LAUNCHER" remove-desktop
else
  systemctl --user disable --now svodka-collect.timer 2>/dev/null
  rm -f "$HOME/.config/systemd/user/svodka-collect.service" "$HOME/.config/systemd/user/svodka-collect.timer"
  systemctl --user daemon-reload 2>/dev/null
  rm -f "$HOME/.local/share/applications/svodka.desktop" "$HOME/.config/autostart/svodka.desktop"
  rm -f "$HOME"/.local/share/icons/hicolor/*/apps/svodka.png
fi
rm -f "$LAUNCHER"
rm -rf "$ROOT/.venv"
echo "Приложение удалено."

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/svodka"
read -r -p "Удалить и данные (статьи, закладки, обучение ленты) в $DATA? [y/N] " a || true
if [[ "$a" =~ ^[YyДд] ]]; then
  rm -rf "$DATA" "${XDG_CONFIG_HOME:-$HOME/.config}/svodka" "${XDG_CACHE_HOME:-$HOME/.cache}/svodka" \
         "${XDG_STATE_HOME:-$HOME/.local/state}/svodka"
  echo "Данные удалены."
else
  echo "Данные оставлены — при повторной установке лента продолжит с того же места."
fi
