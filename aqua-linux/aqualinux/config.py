"""Настройки и каталоги XDG.

Настройки хранятся в ~/.config/aqua-linux/settings.json, данные (история,
словарь, модели, аудио) — в ~/.local/share/aqua-linux, кэш — в ~/.cache/aqua-linux.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

APP_ID = "aqua-linux"
APP_NAME = "Aqua Linux"
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
        "double_tap_hands_free": True,             # двойное нажатие клавиши = «без рук»
        "tap_threshold_ms": 280,                   # короче — нажатие, длиннее — удержание
        "double_tap_window_ms": 380,
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
        "fuzzy_dictionary": True,
        "fuzzy_threshold": 0.84,
        "casual_messaging": False,     # строчные и без точки в мессенджерах
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
        "provider": "builtin",         # builtin (Qwen3.5-0.8B через llama.cpp) | ollama | openai
        "correct": False,              # улучшать распознанный текст
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
        "autostart": True,
        "privacy_mode": False,         # не сохранять историю
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


class Settings:
    """Потокобезопасный словарь настроек с сохранением в JSON."""

    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners = []
        self.data = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return deep_merge(DEFAULTS, raw)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

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


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, CACHE_DIR, STATE_DIR, MODELS_DIR, AUDIO_DIR):
        path.mkdir(parents=True, exist_ok=True)


def find_model_dir(settings: Settings) -> Path | None:
    """Каталог, где лежат оба файла модели, или None."""
    custom = settings.get("asr.model_dir")
    candidates = ([Path(custom).expanduser()] if custom else []) + MODEL_SEARCH_DIRS
    for directory in candidates:
        if all((directory / name).is_file() for name in MODEL_FILES):
            return directory
    return None
