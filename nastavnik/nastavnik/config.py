"""Настройки и каталоги XDG (паттерн Aqua Linux и «Сводки»).

Настройки — ~/.config/nastavnik/settings.json, данные (база, бэкапы) — ~/.local/share/nastavnik,
кэш — ~/.cache/nastavnik, журнал и служебное — ~/.local/state/nastavnik.
Повреждённый или вручную испорченный файл настроек не ломает приложение: значения
приводятся к допустимым, сломанный файл откладывается в сторону.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path

APP_ID = "nastavnik"
APP_NAME = "Наставник"
APP_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    base = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    return base / APP_ID


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state")
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DB_FILE = DATA_DIR / "nastavnik.sqlite3"
BACKUP_DIR = DATA_DIR / "backups"
LOG_FILE = STATE_DIR / "nastavnik.log"
RUNTIME_DIR = STATE_DIR / "runtime"
# Отдельные рабочие папки для Claude: так его переписки по учёбе и по разговорам лежат
# в разных каталогах ~/.claude/projects/… и разговоры можно удалять, не трогая учёбу.
CLAUDE_LEARN_DIR = STATE_DIR / "claude-learn"
CLAUDE_TALK_DIR = STATE_DIR / "claude-talk"

DEFAULTS: dict = {
    "version": 1,
    # --- Учёба -------------------------------------------------------------
    "learn": {
        "session_minutes": 25,            # обычная длина сессии (дальше подстраивается по усталости)
        "week_goal_minutes": 180,         # цель по времени в неделю
        "new_per_session": 1,             # новых понятий за сессию
        "desired_retention": 0.9,         # FSRS: повторять, когда вероятность вспомнить падает до 90 %
        "explore_share": 0.15,            # доля «разведки» у бандитов форматов
        "context_tokens": 60000,          # после стольких токенов разговор по теме начинается заново
        "streak_freezes_per_week": 1,     # серия дней с «заморозкой»: один пропуск в неделю не рвёт её
    },
    # --- Напоминание -------------------------------------------------------------
    "reminder": {
        "enabled": True,
        "time": "19:07",                  # не ровный час: планировщики перегружены на :00
    },
    # --- Claude -------------------------------------------------------------
    "claude": {
        "command": "claude",
        "model": "sonnet",
        "fallback_model": "haiku",
        "timeout_s": 300,
    },
    # --- Разговор -------------------------------------------------------------
    "talk": {
        "keep_transcripts": False,        # хранить переписку разговоров (по умолчанию — нет)
        "memory": "ask",                  # сводки разговора: ask | always | never
        "default_mode": "listen",         # listen | understand | act
    },
    # --- Интерфейс ------------------------------------------------------------
    "ui": {
        "theme": "auto",
        "text_size": "m",
        "welcome_done": False,
        "tips_seen": [],
        "autostart": False,
        "notifications": True,
    },
    # --- О человеке ------------------------------------------------------------
    "profile": {
        "name": "",
        "interests": "",                  # из них Claude берёт примеры, крючки и подарки
    },
}

RANGES = {
    "learn.session_minutes": (5, 120), "learn.week_goal_minutes": (10, 3000),
    "learn.new_per_session": (0, 5), "learn.desired_retention": (0.7, 0.97),
    "learn.explore_share": (0.0, 0.5), "learn.context_tokens": (8000, 400000),
    "learn.streak_freezes_per_week": (0, 3),
    "claude.timeout_s": (30, 1800),
}
CHOICES = {
    "ui.theme": ("auto", "dark", "light"),
    "ui.text_size": ("s", "m", "l"),
    "talk.memory": ("ask", "always", "never"),
    "talk.default_mode": ("listen", "understand", "act"),
    "claude.model": ("sonnet", "opus", "haiku"),
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
    """Прочитать JSON; повреждённый файл откладываем (*.broken-…) и берём копию *.bak."""
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


def valid_time(value) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        h, m = int(value[:2]), int(value[3:])
    except ValueError:
        return False
    return 0 <= h < 24 and 0 <= m < 60


def sanitize(data: dict, defaults: dict | None = None, prefix: str = "") -> list[str]:
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
            elif ok and isinstance(default, int) and not isinstance(value, int):
                data[key] = int(value)
        elif isinstance(default, str):
            ok = isinstance(value, str) and (path not in CHOICES or value in CHOICES[path])
            if ok and path == "reminder.time" and not valid_time(value):
                ok = False
        elif isinstance(default, list):
            ok = isinstance(value, list)
        if not ok:
            data[key] = copy.deepcopy(default)
            fixed.append(path)
    return fixed


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
        fixed = sanitize(data)
        if fixed:
            logging.getLogger(__name__).warning("Исправлены неверные настройки: %s", ", ".join(fixed))
        return data

    def save(self) -> None:
        with self._lock:
            try:
                write_json_safely(self.path, self.data)
            except OSError:
                pass   # диск заполнен или только чтение — настройки живут в памяти

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


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, STATE_DIR, BACKUP_DIR, RUNTIME_DIR, CLAUDE_LEARN_DIR,
              CLAUDE_TALK_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def setup_logging(level: int = logging.INFO) -> None:
    ensure_dirs()
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level)
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.WARNING)
    root.addHandler(sh)
