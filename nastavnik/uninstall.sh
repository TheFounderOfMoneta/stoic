#!/usr/bin/env bash
# Удаление «Наставника»: таймер, ярлык, автозапуск, команда nastavnik, окружение Python.
# Ваши темы, карточки и то, что система о вас узнала, удаляются только если вы согласитесь.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$HOME/.local/bin/nastavnik"

if [[ -x "$LAUNCHER" ]]; then
  "$LAUNCHER" remove-timer
  "$LAUNCHER" remove-desktop
else
  systemctl --user disable --now nastavnik-remind.timer 2>/dev/null
  rm -f "$HOME/.config/systemd/user/nastavnik-remind.service" "$HOME/.config/systemd/user/nastavnik-remind.timer"
  systemctl --user daemon-reload 2>/dev/null
  rm -f "$HOME/.local/share/applications/nastavnik.desktop" "$HOME/.config/autostart/nastavnik.desktop"
  rm -f "$HOME"/.local/share/icons/hicolor/*/apps/nastavnik.png
fi
rm -f "$LAUNCHER"
rm -rf "$ROOT/.venv"
echo "Приложение удалено."

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/nastavnik"
read -r -p "Удалить и данные (темы, карточки, история учёбы, память разговоров) в $DATA? [y/N] " a || true
if [[ "$a" =~ ^[YyДд] ]]; then
  rm -rf "$DATA" "${XDG_CONFIG_HOME:-$HOME/.config}/nastavnik" "${XDG_CACHE_HOME:-$HOME/.cache}/nastavnik" \
         "${XDG_STATE_HOME:-$HOME/.local/state}/nastavnik"
  echo "Данные удалены."
else
  echo "Данные оставлены — при повторной установке всё продолжится с того же места."
fi
