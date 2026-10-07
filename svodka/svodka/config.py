"""Настройки и каталоги XDG (паттерн Aqua Linux).

Настройки — ~/.config/svodka/settings.json, данные (база, бэкапы) — ~/.local/share/svodka,
кэш (страницы, картинки) — ~/.cache/svodka, журнал — ~/.local/state/svodka.
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

APP_ID = "svodka"
APP_NAME = "Сводка"
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
DB_FILE = DATA_DIR / "svodka.sqlite3"
BACKUP_DIR = DATA_DIR / "backups"
IMAGES_DIR = CACHE_DIR / "images"
LOG_FILE = STATE_DIR / "svodka.log"
LOCK_FILE = STATE_DIR / "collect.lock"
RUNTIME_DIR = STATE_DIR / "runtime"

DEFAULTS: dict = {
    "version": 1,
    "onboarding_done": False,
    # --- Сбор -------------------------------------------------------------
    "schedule": {
        "enabled": True,
        "times": ["07:37", "18:37"],      # не ровный час: планировщики перегружены на :00
        "catchup_hours": 10,              # сбор старше — догнать при запуске и после сна
    },
    "collect": {
        "articles_per_run": 15,
        "quota_core": 0.7,                # ваши интересы
        "quota_explore": 0.2,             # разведка соседних тем
        "quota_world": 0.1,               # главное в мире вне профиля
        "max_turns": 80,
        "timeout_min": 25,
        "max_age_hours": 72,              # старше — не берём: лента о свежем, а не о прошлой неделе
    },
    # --- Claude -------------------------------------------------------------
    "claude": {
        "command": "claude",
        "model": "sonnet",
        "fallback_model": "haiku",
    },
    # --- Перевод -------------------------------------------------------------
    "translate": {
        "prefetch_top": 5,                # сразу после сбора переводить целиком лучшие N
        "chunk_words": 1500,
        "model": "sonnet",
    },
    # --- Обучение -------------------------------------------------------------
    "learning": {
        "personalization": True,          # выключить — лента без подстройки (для сравнения)
        "survey_every": 4,                # опрос примерно после каждой N-й прочитанной
        "survey_max_per_day": 3,
        "randomize_top_prob": 0.1,        # иногда меняем местами две верхние — честная оценка позиции
        "explore_slots": 1,
        "explore_slots_first_week": 2,
        "weekly_review": True,
    },
    # --- Хранение -------------------------------------------------------------
    "storage": {
        "keep_days": 30,                  # сохранённое хранится всегда
        "image_cache_mb": 500,
        "backups": 3,
    },
    # --- Интерфейс ------------------------------------------------------------
    "ui": {
        "theme": "auto",
        "text_size": "m",
        "welcome_done": False,
        "tips_seen": [],
        "short_collapsed": False,         # блок «Коротко» в Читалке свернуть навсегда
        "autostart": True,
        "notifications": True,
    },
}

RANGES = {
    "schedule.catchup_hours": (1, 72),
    "collect.articles_per_run": (3, 60), "collect.quota_core": (0.0, 1.0),
    "collect.quota_explore": (0.0, 1.0), "collect.quota_world": (0.0, 1.0),
    "collect.max_turns": (10, 300), "collect.timeout_min": (3, 120), "collect.max_age_hours": (12, 336),
    "translate.prefetch_top": (0, 30), "translate.chunk_words": (300, 5000),
    "learning.survey_every": (1, 50), "learning.survey_max_per_day": (0, 20),
    "learning.randomize_top_prob": (0.0, 0.5), "learning.explore_slots": (0, 5),
    "learning.explore_slots_first_week": (0, 5),
    "storage.keep_days": (3, 3650), "storage.image_cache_mb": (50, 20000), "storage.backups": (0, 30),
}
CHOICES = {
    "ui.theme": ("auto", "dark", "light"),
    "ui.text_size": ("s", "m", "l"),
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


def _valid_time(value) -> bool:
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
        elif isinstance(default, list):
            ok = isinstance(value, list)
            if ok and path == "schedule.times":
                clean = sorted({v for v in value if _valid_time(v)})
                if clean != value:
                    data[key] = clean or copy.deepcopy(default)
                    fixed.append(path)
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


RESTART_CODE = 75
RESTARTS_FILE = STATE_DIR / "restarts.json"


def restart_allowed(window_s: float = 1800, limit: int = 2) -> bool:
    """Самоперезапуск — не чаще limit раз за window_s."""
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
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, STATE_DIR, IMAGES_DIR, BACKUP_DIR, RUNTIME_DIR):
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
