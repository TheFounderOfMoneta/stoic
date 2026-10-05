#!/usr/bin/env bash
# Установка Aqua Linux для текущего пользователя (без root, кроме системных пакетов apt).
#
#   bash install.sh                  — обычная установка (PyTorch 2.6.0 + CUDA 12.4, ~2,5 ГБ)
#   bash install.sh --torch-from DIR — взять PyTorch из уже готового окружения GigaAM (.venv)
#   bash install.sh --cpu            — PyTorch только для процессора (~200 МБ)
#   bash install.sh --yes            — не задавать вопросов
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP=aqua-linux
VENV="$ROOT/.venv"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
BIN_DIR="$HOME/.local/bin"
TORCH_VERSION=2.6.0
TORCH_INDEX_CUDA="https://download.pytorch.org/whl/cu124"
TORCH_INDEX_CPU="https://download.pytorch.org/whl/cpu"
KNOWN_GIGAAM="/home/vlad/Documents/Codex/2026-10-05/new-chat-3/outputs/gigaam"

TORCH_FROM=""
CPU_ONLY=0
ASSUME_YES=0
while (( $# )); do
    case "$1" in
        --torch-from) TORCH_FROM="${2:?укажите путь к .venv}"; shift 2 ;;
        --cpu) CPU_ONLY=1; shift ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✕\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { (( ASSUME_YES )) && return 0; read -r -p "$1 [Д/н] " a; [[ -z "$a" || "$a" =~ ^[ДдYy] ]]; }

[[ "$(uname -m)" == "x86_64" ]] || die "Поддерживается только x86_64"
command -v python3.12 >/dev/null || die "Нужен Python 3.12 (python3.12)"
[[ $EUID -ne 0 ]] || die "Запускайте без sudo — установка идёт в домашний каталог"

# ---------------------------------------------------------------- уже установлено?
LAUNCHER="$BIN_DIR/$APP"
PREV_ROOT=""
WAS_RUNNING=0
if [[ -f "$LAUNCHER" ]]; then
    PREV_ROOT="$(sed -n 's/^export PYTHONPATH="\([^$"]*\).*/\1/p' "$LAUNCHER" | head -n1)"
fi
if [[ -n "$PREV_ROOT" ]]; then
    say "Aqua Linux уже установлена: $PREV_ROOT"
    if [[ "$PREV_ROOT" == "$ROOT" ]]; then
        echo "    Обновляю на месте — готовые шаги пропускаются, ничего лишнего не скачивается."
    else
        echo "    Ставлю новую версию из $ROOT. Настройки, история, словарь и модели сохранятся."
    fi
elif [[ -x "$VENV/bin/python" ]]; then
    say "Нашёл незавершённую установку в $ROOT — продолжаю с того же места"
fi
if pgrep -u "$(id -u)" -f -- "-m aqualinux( |$)" >/dev/null 2>&1; then
    WAS_RUNNING=1
    say "Aqua Linux сейчас запущена — закрываю её на время обновления"
    "$LAUNCHER" quit >/dev/null 2>&1 || true
    for _ in $(seq 1 50); do
        pgrep -u "$(id -u)" -f -- "-m aqualinux( |$)" >/dev/null 2>&1 || break
        sleep 0.1
    done
    if pgrep -u "$(id -u)" -f -- "-m aqualinux( |$)" >/dev/null 2>&1; then
        pkill -u "$(id -u)" -f -- "-m aqualinux( |$)" || true
        sleep 0.5
    fi
fi

# ---------------------------------------------------------------- системные пакеты
say "Проверяю системные пакеты"
need=()
for pkg in python3.12-venv libportaudio2 libxcb-cursor0 xdotool; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        need+=("$pkg")
    fi
done
if (( ${#need[@]} )); then
    warn "Не хватает: ${need[*]}"
    echo "    python3.12-venv — виртуальное окружение; libportaudio2 — микрофон;"
    echo "    libxcb-cursor0 — нужен Qt 6 под X11; xdotool — запасной способ вставки текста."
    if ask "Установить их через sudo apt?"; then
        sudo apt-get update -q
        sudo apt-get install -y "${need[@]}"
    else
        die "Без этих пакетов приложение не запустится"
    fi
fi
ok "Системные пакеты на месте"

# ---------------------------------------------------------------- окружение Python
if [[ ! -x "$VENV/bin/python" && -n "$PREV_ROOT" && "$PREV_ROOT" != "$ROOT" && -x "$PREV_ROOT/.venv/bin/python" ]]; then
    if ask "Перенести готовое окружение (PyTorch, PySide6) из $PREV_ROOT, чтобы не скачивать заново?"; then
        mv "$PREV_ROOT/.venv" "$VENV"
        ok "Окружение перенесено"
    fi
fi
FRESH_VENV=0
if [[ ! -x "$VENV/bin/python" ]]; then
    say "Создаю окружение $VENV"
    python3.12 -m venv "$VENV"
    FRESH_VENV=1
fi
PY="$VENV/bin/python"
if (( FRESH_VENV )); then
    "$PY" -m pip install -q --upgrade pip wheel
fi

# ---------------------------------------------------------------- PyTorch
site_of() { "$1/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'; }
TORCH_READY=0
if [[ -z "$TORCH_FROM" && $CPU_ONLY -eq 0 ]] && "$PY" -c "import torch, torchaudio" 2>/dev/null; then
    TORCH_READY=1
    ok "PyTorch уже есть ($("$PY" -c 'import torch; print(torch.__version__)' 2>/dev/null)) — пропускаю"
fi
if (( ! TORCH_READY )) && [[ -z "$TORCH_FROM" && $CPU_ONLY -eq 0 && -x "$KNOWN_GIGAAM/.venv/bin/python" ]]; then
    if "$KNOWN_GIGAAM/.venv/bin/python" -c "import torch, torchaudio" 2>/dev/null; then
        say "Найдено готовое окружение GigaAM: $KNOWN_GIGAAM/.venv"
        if ask "Взять PyTorch оттуда (экономит ~2,5 ГБ загрузки)?"; then
            TORCH_FROM="$KNOWN_GIGAAM/.venv"
        fi
    fi
fi
PTH="$(site_of "$VENV")/zz_external_torch.pth"
if (( TORCH_READY )); then
    :
elif [[ -n "$TORCH_FROM" ]]; then
    [[ -x "$TORCH_FROM/bin/python" ]] || die "Нет $TORCH_FROM/bin/python"
    "$TORCH_FROM/bin/python" -c "import sys; assert sys.version_info[:2] == (3, 12)" \
        || die "Окружение $TORCH_FROM должно быть на Python 3.12"
    "$TORCH_FROM/bin/python" -c "import torch, torchaudio" || die "В $TORCH_FROM нет torch/torchaudio"
    # .pth в конце sys.path: наши пакеты важнее, torch берётся из готового окружения.
    site_of "$TORCH_FROM" > "$PTH"
    ok "PyTorch подключён из $TORCH_FROM (без копирования)"
else
    rm -f "$PTH"
    if "$PY" -c "import torch, torchaudio; assert torch.__version__.startswith('$TORCH_VERSION')" 2>/dev/null; then
        ok "PyTorch $TORCH_VERSION уже установлен"
    elif (( CPU_ONLY )); then
        say "Ставлю PyTorch $TORCH_VERSION (CPU)"
        "$PY" -m pip install --index-url "$TORCH_INDEX_CPU" "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
    else
        say "Ставлю PyTorch $TORCH_VERSION + CUDA 12.4 (~2,5 ГБ, проверено на GTX 1650 Ti)"
        "$PY" -m pip install --index-url "$TORCH_INDEX_CUDA" "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
    fi
fi

reqs_ok() {
    "$PY" - "$ROOT/requirements.txt" <<'PYEOF' 2>/dev/null
import sys
from importlib.metadata import PackageNotFoundError, version
from packaging.requirements import Requirement
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    req = Requirement(line)
    try:
        have = version(req.name)
    except PackageNotFoundError:
        sys.exit(1)
    if req.specifier and not req.specifier.contains(have, prereleases=True):
        sys.exit(1)
version("silero-vad")
PYEOF
}
if reqs_ok; then
    ok "Зависимости приложения уже установлены — пропускаю"
else
    say "Ставлю зависимости приложения (PySide6 ~100 МБ, пара минут)"
    "$PY" -m pip install --progress-bar on -r "$ROOT/requirements.txt"
    "$PY" -m pip install -q --no-deps "silero-vad==6.2.3"
    ok "Зависимости установлены"
fi

# ---------------------------------------------------------------- веса модели
say "Ищу веса GigaAM v3 E2E RNNT"
mkdir -p "$DATA_DIR/models"
found=""
for dir in "$DATA_DIR/models" "$KNOWN_GIGAAM/models" "$HOME/.cache/gigaam" "$ROOT/models"; do
    if [[ -f "$dir/v3_e2e_rnnt.ckpt" && -f "$dir/v3_e2e_rnnt_tokenizer.model" ]]; then
        found="$dir"; break
    fi
done
if [[ -z "$found" ]]; then
    found_ckpt="$(find "$HOME" -xdev -name v3_e2e_rnnt.ckpt -size +100M 2>/dev/null | head -n1 || true)"
    if [[ -n "$found_ckpt" && -f "$(dirname "$found_ckpt")/v3_e2e_rnnt_tokenizer.model" ]]; then
        found="$(dirname "$found_ckpt")"
    fi
fi
if [[ -n "$found" && "$found" != "$DATA_DIR/models" ]]; then
    ln -sf "$found/v3_e2e_rnnt.ckpt" "$DATA_DIR/models/v3_e2e_rnnt.ckpt"
    ln -sf "$found/v3_e2e_rnnt_tokenizer.model" "$DATA_DIR/models/v3_e2e_rnnt_tokenizer.model"
    ok "Веса найдены в $found — подключены ссылками в $DATA_DIR/models"
elif [[ -n "$found" ]]; then
    ok "Веса уже в $DATA_DIR/models"
else
    warn "Веса не найдены — приложение скачает их при первом запуске (~0,9 ГБ) с CDN SberDevices"
fi

# ---------------------------------------------------------------- ИИ
say "ИИ для улучшения текста"
LLM() { PYTHONPATH="$ROOT" "$PY" -m aqualinux.localllm "$@"; }
HAS_KEY="$(PYTHONPATH="$ROOT" "$PY" -c 'from aqualinux.config import Settings; print(1 if (Settings().get("llm.deepseek_key") or "").strip() else 0)' 2>/dev/null || echo 0)"
if [[ "$HAS_KEY" == "1" ]]; then
    ok "Ключ DeepSeek уже вставлен — основной ИИ готов"
elif (( ! ASSUME_YES )); then
    echo "    Основной ИИ — DeepSeek API: быстро, качественно, копейки за запрос, видеокарта не нужна."
    echo "    Ключ: platform.deepseek.com → API keys. Можно пропустить и вставить позже в Настройках."
    LLM deepseek || true
fi
# Запасная модель — на случай, если нет интернета (уже установленное используется без загрузки).
if LLM configured >/dev/null 2>&1; then
    ok "Запасная локальная модель уже готова — пропускаю"
else
    AI_DONE=0
    if command -v ollama >/dev/null || curl -s -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ok "Найдена Ollama — запасной будет она (ничего не скачивается)"
        if LLM ollama-setup; then AI_DONE=1; fi
    fi
    if (( ! AI_DONE )) && ask "Поставить запасную локальную модель Qwen3.5-0.8B (~0,5 ГБ, работает без интернета)? Уже установленные CUDA, llama-server и GGUF используются без загрузки"; then
        LLM install --enable || warn "Запасная модель не установилась — можно позже в Настройках"
    fi
fi

# ---------------------------------------------------------------- ярлыки
say "Создаю ярлыки"
mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
cat > "$BIN_DIR/$APP" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PY" -m aqualinux "\$@"
EOF
chmod +x "$BIN_DIR/$APP"
PYTHONPATH="$ROOT" QT_QPA_PLATFORM=offscreen "$PY" -m aqualinux.selfcheck --icon "$ICON_DIR/$APP.png"
cat > "$APPS_DIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Aqua Linux
GenericName=Голосовая диктовка
Comment=Говорите — текст появляется там, где курсор (GigaAM, локально)
Exec=$BIN_DIR/$APP
Icon=$APP
Terminal=false
Categories=Utility;Accessibility;AudioVideo;
Keywords=voice;dictation;speech;голос;диктовка;
StartupNotify=false
StartupWMClass=aqua-linux
Actions=Toggle;Settings;

[Desktop Action Toggle]
Name=Начать/остановить диктовку
Exec=$BIN_DIR/$APP toggle

[Desktop Action Settings]
Name=Настройки
Exec=$BIN_DIR/$APP settings
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "${ICON_DIR%/256x256/apps}" 2>/dev/null || true
ok "Ярлык «Aqua Linux» добавлен в меню приложений, команда: $APP"

# ---------------------------------------------------------------- проверка
say "Самопроверка"
PYTHONPATH="$ROOT" "$PY" -m aqualinux.selfcheck || warn "Самопроверка нашла проблемы (см. выше)"

if command -v gnome-extensions >/dev/null; then
    if ! gnome-extensions list --enabled 2>/dev/null | grep -qi appindicator; then
        warn "Расширение AppIndicator выключено — значка в трее не будет."
        echo "    Включите: gnome-extensions enable ubuntu-appindicators@ubuntu.com"
    fi
fi
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    warn "Сейчас Wayland. Удержание клавиши работает только в «Ubuntu на Xorg» (выбирается на экране входа)."
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR не в PATH — запускайте из меню или полным путём" ;; esac

echo
ok "Готово! Запуск: $APP   (или «Aqua Linux» в меню приложений)"
echo "   Удерживайте Правый Alt и говорите. Отпустите — текст вставится."
echo "   Коротко нажмите Правый Alt (или щёлкните по капсуле) — длинная запись без удержания."
if (( WAS_RUNNING )); then
    say "Запускаю обновлённую версию"
    nohup "$LAUNCHER" --background >/dev/null 2>&1 &
elif [[ -n "${DISPLAY:-}" ]] && ask "Запустить Aqua Linux сейчас?"; then
    nohup "$LAUNCHER" >/dev/null 2>&1 &
fi
