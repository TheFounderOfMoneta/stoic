#!/usr/bin/env bash
# Установка «Сводки» на Ubuntu (22.04 и новее): окружение Python, ярлык в меню, таймер сбора,
# проверка Claude Code. Можно запускать повторно — это же обновление.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
BIN="$HOME/.local/bin"
LAUNCHER="$BIN/svodka"

say()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { [[ -t 0 ]] || return 1; local a; read -r -p "$1 [Y/n] " a || true; [[ -z "$a" || "$a" =~ ^[YyДд] ]]; }
SUDO=""; [[ $EUID -eq 0 ]] || SUDO="sudo"

# ---------------------------------------------------------------- системные пакеты
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || fail "Нужен python3 (sudo apt install python3)."
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Нужен Python 3.10 или новее."

need=()
"$PY" -c 'import venv, ensurepip' 2>/dev/null || need+=("python3-venv")
# Qt 6.5+ не запускается под X11 без libxcb-cursor0; остальное обычно уже есть в Ubuntu Desktop.
for lib in libxcb-cursor0 libegl1 libxkbcommon-x11-0 libnotify-bin; do
  dpkg -s "$lib" >/dev/null 2>&1 || need+=("$lib")
done
if ((${#need[@]})); then
  say "Нужны системные пакеты: ${need[*]}"
  if command -v apt-get >/dev/null; then
    $SUDO apt-get install -y "${need[@]}" || warn "Не удалось поставить пакеты — попробуйте вручную: sudo apt install ${need[*]}"
  else
    warn "Поставьте их вручную: ${need[*]}"
  fi
fi

# ---------------------------------------------------------------- окружение Python
say "Готовлю окружение Python в $VENV"
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"
ok "Зависимости установлены"

# ---------------------------------------------------------------- команда svodka и ярлык
mkdir -p "$BIN"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Запуск «Сводки» (создано install.sh)
export PYTHONPATH="$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m svodka "\$@"
EOF
chmod +x "$LAUNCHER"
"$LAUNCHER" install-desktop >/dev/null
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
ok "Команда svodka и ярлык «Сводка» в меню приложений"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN нет в PATH — откройте новый терминал или перезайдите в систему." ;; esac

# ---------------------------------------------------------------- Claude Code
CLAUDE="$(command -v claude || true)"
[[ -z "$CLAUDE" && -x "$HOME/.local/bin/claude" ]] && CLAUDE="$HOME/.local/bin/claude"
if [[ -z "$CLAUDE" ]]; then
  warn "Claude Code не найден. «Сводка» работает через него по вашей подписке Pro."
  if ask "Установить Claude Code сейчас (curl -fsSL https://claude.ai/install.sh | bash)?"; then
    curl -fsSL https://claude.ai/install.sh | bash
    CLAUDE="$HOME/.local/bin/claude"
  fi
fi
if [[ -n "$CLAUDE" && -x "$CLAUDE" ]]; then
  if "$CLAUDE" auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    ok "Claude Code: вход выполнен"
  else
    warn "Нужно войти в Claude тем же аккаунтом, что и на claude.ai (подписка Pro)."
    if ask "Войти сейчас?"; then
      "$CLAUDE" auth login || warn "Вход не завершён — это можно сделать позже из приложения."
    fi
  fi
else
  warn "Без Claude Code лента не соберётся. Установить можно позже — приложение подскажет как."
fi

# ---------------------------------------------------------------- расписание
if command -v systemctl >/dev/null; then
  if "$LAUNCHER" install-timer; then
    ok "Сбор по расписанию (утром и вечером; если ПК был выключен — после включения)"
  else
    warn "Таймер не поставился — пока окно открыто, «Сводка» соберёт ленту сама."
  fi
fi

echo
ok "Готово. Запустите «Сводку» из меню приложений или командой: svodka"
echo "   При первом запуске короткий мастер настроит темы и соберёт первую ленту (3–6 минут)."
