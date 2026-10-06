"""Настройки и каталоги XDG.

Настройки хранятся в ~/.config/aqua-linux/settings.json, данные (история,
словарь, модели, аудио) — в ~/.local/share/aqua-linux, кэш — в ~/.cache/aqua-linux.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path

APP_ID = "aqua-linux"
APP_NAME = "Aqua Linux"
APP_VERSION = "1.1.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    base = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    return base / APP_ID


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state")
SETTINGS_FILE = CONFIG_DIR / "settings.json"
MODELS_DIR = DATA_DIR / "models"
AUDIO_DIR = DATA_DIR / "audio"
HISTORY_DB = DATA_DIR / "history.sqlite3"
LOG_FILE = STATE_DIR / "aqua-linux.log"

MODEL_NAME = "v3_e2e_rnnt"
MODEL_FILES = (f"{MODEL_NAME}.ckpt", f"{MODEL_NAME}_tokenizer.model")

# Где искать уже скачанные веса, если их ещё нет в MODELS_DIR.
MODEL_SEARCH_DIRS = [
    MODELS_DIR,
    Path.home() / ".cache/gigaam",
    Path("/home/vlad/Documents/Codex/2026-10-05/new-chat-3/outputs/gigaam/models"),
    PROJECT_ROOT / "models",
]

DEFAULTS: dict = {
    "version": 1,
    "onboarding_done": False,
    # --- Горячие клавиши (как в Aqua Voice для Windows: удерживать Alt) ------
    # Токены: ctrl/lctrl/rctrl, shift/…, alt/lalt/ralt, super/lsuper/rsuper,
    # space, escape, f1..f24, буквы/цифры (по латинской раскладке), mouse8/mouse9 …
    "hotkeys": {
        "activate": [["ralt"]],                    # удерживать — говорить, отпустить — вставить
        "hands_free": [],                          # отдельное сочетание «без рук» (необязательно)
        "paste_last": [["ctrl", "super", "v"]],    # вставить последнюю диктовку (как Cmd+Ctrl+V)
        "cancel": [["escape"]],                    # отмена во время записи
        "double_tap_hands_free": True,             # короткое нажатие (или двойное) = длинная запись «без рук»
        "tap_threshold_ms": 280,                   # короче — нажатие, длиннее — удержание
        "double_tap_window_ms": 380,               # второе нажатие в этом окне не останавливает запись
        "neutralize_modifier": True,               # чтобы одиночный Alt не открывал меню в Firefox
        "enabled": True,
    },
    # --- Звук -----------------------------------------------------------
    "audio": {
        "input_device": None,          # None = системный по умолчанию (PipeWire)
        "keep_mic_warm": False,        # держать микрофон открытым (быстрее старт, горит индикатор)
        "tail_ms": 140,                # дописывать после отпускания клавиши
        "sounds": True,                # Play Sounds
        "sound_volume": 0.35,
        "while_dictating": "mute",     # Mute Background Audio: none | mute | pause
        "max_minutes": 20,
        "save_audio": True,            # аудио в истории (как в Aqua — 3 дня)
        "keep_audio_days": 3,
        "warn_clipping": True,         # предупреждать, если микрофон перегружен
    },
    # --- Распознавание ----------------------------------------------------
    "asr": {
        "device": "auto",              # auto | cuda | cpu
        "precision": "auto",           # auto | int8 | fp16 | fp32
        "cpu_threads": 6,
        "model_dir": "",               # пусто = ~/.local/share/aqua-linux/models
        "streaming": "hands_free",     # Streaming Mode: never | hands_free | always
        "preview_interval_ms": 600,
        "unload_after_min": 0,         # 0 = держать модель в памяти
        "warmup": True,
    },
    # --- Вставка текста --------------------------------------------------
    "insert": {
        "method": "paste",             # paste | type (xdotool) | clipboard
        "restore_clipboard": True,     # Avoid Clipboard History
        "restore_delay_ms": 450,
        "trailing_space": True,
        "terminal_shift_paste": True,
        "send_it": True,               # «Отправь» в конце (режим без рук) → нажать Enter
        "send_key": "enter",           # enter | ctrl+enter | none
    },
    # --- Обработка текста ------------------------------------------------
    "text": {
        "remove_fillers": True,
        "voice_commands": True,        # «новая строка», «новый абзац»
        "fuzzy_dictionary": False,     # нечёткий поиск по словарю (термины по смыслу подставляет ИИ)
        "fuzzy_threshold": 0.84,
        "casual_messaging": False,     # строчные и без точки в мессенджерах
        "builtin_terms": False,        # встроенная база ~4 тыс. терминов (без ИИ); с ИИ термины подставляет он
        "numbers": True,               # «номер один» → «№ 1», «двадцать пять» → «25»
        "number_style": "sign",        # sign («№ 5») | word («номер 5»)
    },
    # --- Плавающая панель («облачко») -------------------------------------
    "bubble": {
        "show": True,                  # Show Floating Bar
        "style": "glass",              # glass (Liquid Glass) | classic (чёрная панель)
        "position": "bottom",          # bottom | top
        "offset_x": 0,                 # смещение после перетаскивания
        "offset_y": 0,
        "margin": "auto",              # auto = над нижним доком (dash-to-dock), иначе пиксели
        "follow_mouse_screen": True,
        "demo_mode": False,            # подпись «Диктовка с Aqua Linux» для записи экрана
        "accent": "#3A8DFF",
    },
    # --- ИИ: улучшение текста и Edit Mode ----------------------------------
    "llm": {
        # Основной ИИ — DeepSeek API (если вставлен ключ), локальная модель — запасная.
        "cloud": True,                 # использовать DeepSeek, когда есть ключ
        "deepseek_key": "",
        "deepseek_model": "deepseek-flash",
        "deepseek_url": "https://api.deepseek.com",
        "cloud_timeout_ms": 4000,      # DeepSeek не ответил за это время — запасная модель/исходный текст
        "provider": "builtin",         # запасная/локальная: builtin (Qwen3.5-0.8B, llama.cpp) | ollama | openai
        "correct": False,              # улучшать распознанный текст
        "style": "fix",                # fix — исправлять ошибки | polish — улучшать структуру и стиль
        "correct_timeout_ms": 2500,    # не успела — вставляем исходный текст
        "correct_min_words": 4,        # короткие фразы не трогаем
        "enabled": False,              # Edit Mode (правка выделенного голосом)
        "builtin_backend": "auto",     # auto | cuda | vulkan | cpu
        "builtin_repo": "",            # свой репозиторий GGUF на Hugging Face (необязательно)
        "builtin_quant": "Q4_K_M",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen3.5:0.8b-local",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "qwen2.5:7b-instruct",
        "timeout_s": 15,
        "use_context": False,
        "instructions": "",
        "app_styles": {},
    },
    "edit_mode": {
        "enabled": True,               # выделили текст + клавиша = правка голосом
        "max_chars": 6000,
    },
    "ui": {
        "theme": "auto",               # auto (как в GNOME) | dark | light
        "glass": False,                # полупрозрачное окно для Blur My Shell
        "welcome_done": False,         # мастер первого запуска пройден
        "tips_seen": [],               # страницы, где подсказки уже показаны
    },
    "general": {
        "performance": "auto",         # auto (подстройка под ПК и питание) | quality | economy
        "autostart": True,
        "privacy_mode": False,         # не сохранять историю
        "history_days": 7,             # хранить историю N дней (0 — всегда); старые записи удаляются сами
        "typing_wpm_baseline": 40,
    },
}

def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def read_json_safely(path: Path):
    """Прочитать JSON; повреждённый файл не ломает приложение: откладываем его в сторону
    (*.broken) и берём последнюю исправную копию (*.bak). None — ничего нет."""
    for candidate in (path, path.with_name(path.name + ".bak")):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            try:
                broken = candidate.with_name(candidate.name + f".broken-{int(time.time())}")
                os.replace(candidate, broken)
                logging.getLogger(__name__).warning("Файл %s повреждён — сохранён как %s", candidate, broken)
            except OSError:
                pass
    return None


def write_json_safely(path: Path, data) -> None:
    """Атомарная запись + резервная копия предыдущей исправной версии."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if path.exists():
        try:
            os.replace(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


SETTINGS_VERSION = 2

# Допустимые значения: настройки правят руками, переносят между версиями, файл может
# повредиться — неверное значение не должно ломать приложение (например, «max_minutes: 0»
# мгновенно обрывала бы каждую запись).
RANGES = {
    "hotkeys.tap_threshold_ms": (60, 1500), "hotkeys.double_tap_window_ms": (100, 1500),
    "audio.tail_ms": (0, 2000), "audio.sound_volume": (0.0, 1.0), "audio.max_minutes": (1, 180),
    "audio.keep_audio_days": (1, 3650), "asr.cpu_threads": (0, 64), "asr.preview_interval_ms": (200, 10000),
    "asr.unload_after_min": (0, 1440), "insert.restore_delay_ms": (80, 5000),
    "text.fuzzy_threshold": (0.5, 1.0), "llm.cloud_timeout_ms": (800, 60000),
    "llm.correct_timeout_ms": (500, 60000), "llm.correct_min_words": (0, 100), "llm.timeout_s": (2, 600),
    "edit_mode.max_chars": (100, 200000), "general.history_days": (0, 3650),
    "general.typing_wpm_baseline": (5, 300),
}
CHOICES = {
    "audio.while_dictating": ("none", "mute", "pause"), "asr.device": ("auto", "cuda", "cpu"),
    "asr.precision": ("auto", "int8", "fp16", "fp32"), "asr.streaming": ("never", "hands_free", "always"),
    "insert.method": ("paste", "type", "clipboard"), "insert.send_key": ("enter", "ctrl+enter", "none"),
    "text.number_style": ("sign", "word"), "bubble.position": ("bottom", "top"),
    "bubble.style": ("glass", "classic"), "llm.style": ("fix", "polish"),
    "llm.provider": ("builtin", "ollama", "openai", "external"), "llm.builtin_backend": ("auto", "cuda", "vulkan", "cpu"),
    "ui.theme": ("auto", "dark", "light"), "general.performance": ("auto", "quality", "economy"),
}
HOTKEY_LISTS = ("activate", "hands_free", "paste_last", "cancel")


def sanitize(data: dict, defaults: dict = None, prefix: str = "") -> list[str]:
    """Привести типы и диапазоны к допустимым. Возвращает исправленные ключи."""
    defaults = DEFAULTS if defaults is None else defaults
    fixed: list[str] = []
    for key, default in defaults.items():
        if key not in data:
            continue
        path = prefix + key
        value = data[key]
        ok = True
        if isinstance(default, dict):
            if not isinstance(value, dict):
                ok = False
            elif default:
                fixed += sanitize(value, default, path + ".")
        elif default is None:
            continue
        elif isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, (int, float)):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
                and abs(value) != float("inf")
            if ok and path in RANGES:
                lo, hi = RANGES[path]
                if not lo <= value <= hi:
                    data[key] = type(default)(min(max(value, lo), hi))
                    fixed.append(path)
        elif isinstance(default, str):
            ok = isinstance(value, str) and (path not in CHOICES or value in CHOICES[path])
        elif isinstance(default, list):
            ok = isinstance(value, list)
            if ok and prefix == "hotkeys." and key in HOTKEY_LISTS:
                clean = [c for c in value if isinstance(c, list) and c and all(isinstance(t, str) and t for t in c)]
                if len(clean) != len(value):
                    data[key] = clean if clean or key != "activate" else copy.deepcopy(default)
                    fixed.append(path)
        if not ok:
            data[key] = copy.deepcopy(default)
            fixed.append(path)
    return fixed


def migrate_settings(data: dict, version: int) -> None:
    """Обновление сохранённых настроек при смене поведения по умолчанию."""
    if version < 2:
        # Термины теперь подставляет ИИ по смыслу; встроенная подстановка и нечёткий поиск —
        # по желанию (они иногда заменяли обычные слова и добавляли задержку).
        text = data.setdefault("text", {})
        text["builtin_terms"] = False
        text["fuzzy_dictionary"] = False


class Settings:
    """Потокобезопасный словарь настроек с сохранением в JSON."""

    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners = []
        self.data = self._load()

    def _load(self) -> dict:
        raw = read_json_safely(self.path)
        if not isinstance(raw, dict):
            raw = {}
        data = deep_merge(DEFAULTS, raw)
        try:
            version = int(raw.get("settings_version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        if raw and version < SETTINGS_VERSION:
            migrate_settings(data, version)
        fixed = sanitize(data)
        if fixed:
            logging.getLogger(__name__).warning("Исправлены неверные настройки: %s", ", ".join(fixed))
        data["settings_version"] = SETTINGS_VERSION
        return data

    def save(self) -> None:
        with self._lock:
            try:
                write_json_safely(self.path, self.data)
            except OSError:
                pass   # диск заполнен/только чтение — настройки остаются в памяти, приложение работает

    def get(self, dotted: str, default=None):
        with self._lock:
            node = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value, save: bool = True) -> None:
        with self._lock:
            node = self.data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        if save:
            self.save()
        for callback in list(self._listeners):
            try:
                callback(dotted, value)
            except Exception:  # noqa: BLE001 — слушатель не должен ронять приложение
                pass

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def reset_section(self, section: str) -> None:
        self.set(section, copy.deepcopy(DEFAULTS[section]))


RESTART_CODE = 75          # приложение просит сторожа перезапустить его
RESTARTS_FILE = STATE_DIR / "restarts.json"


def restart_allowed(window_s: float = 1800, limit: int = 2) -> bool:
    """Самоперезапуск — не чаще limit раз за window_s: если причина не уходит, по кругу не перезапускаемся."""
    data = read_json_safely(RESTARTS_FILE) if RESTARTS_FILE.exists() else None
    now = time.time()
    recent = [r for r in (data or []) if isinstance(r, dict) and now - float(r.get("ts", 0) or 0) < window_s]
    return len(recent) < limit


def note_restart(reason: str) -> None:
    data = read_json_safely(RESTARTS_FILE) if RESTARTS_FILE.exists() else None
    items = [r for r in (data or []) if isinstance(r, dict)][-9:]
    items.append({"ts": time.time(), "reason": reason})
    try:
        write_json_safely(RESTARTS_FILE, items)
    except OSError:
        pass


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, CACHE_DIR, STATE_DIR, MODELS_DIR, AUDIO_DIR):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:   # вместо папки файл, нет прав — работаем без неё
            logging.getLogger(__name__).warning("Не удалось создать %s: %s", path, exc)


def find_model_dir(settings: Settings) -> Path | None:
    """Каталог, где лежат оба файла модели, или None."""
    custom = settings.get("asr.model_dir")
    candidates = ([Path(custom).expanduser()] if custom else []) + MODEL_SEARCH_DIRS
    for directory in candidates:
        if all((directory / name).is_file() for name in MODEL_FILES):
            return directory
    return None
