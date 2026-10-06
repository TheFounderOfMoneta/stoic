"""Сбор новостей и поиск в интернете — запуск Claude Code с MCP-сервером приложения.

Запускается таймером systemd (утром и вечером, с догоном пропущенного после включения ПК),
окном при запуске/после сна (если сбор устарел) и кнопками «Собрать сейчас» / «Искать с Claude».
Двойной запуск невозможен: блокировка файла + проверка «недавно уже собирали».
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import claude_cli
from .config import BACKUP_DIR, DB_FILE, LOCK_FILE, PROJECT_ROOT, PROMPTS_DIR, RUNTIME_DIR, ensure_dirs

log = logging.getLogger(__name__)

RECENT_SKIP_HOURS = 3     # плановый сбор пропускается, если удачный был недавно


class Busy(Exception):
    pass


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise Busy("сбор уже идёт")
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"{os.getpid()} {time.time()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        except OSError:
            pass


def is_running() -> bool:
    """Идёт ли сбор прямо сейчас (в любом процессе)."""
    try:
        with _Lock(LOCK_FILE):
            return False
    except Busy:
        return True
    except OSError:
        return False


def due(settings, storage, now: float | None = None) -> bool:
    """Пора ли догнать сбор (ПК был выключен в плановое время)."""
    now = now if now is not None else time.time()
    if not settings.get("schedule.enabled", True):
        return False
    last = storage.last_run("collect")
    if not last:
        return True
    return now - float(last["started"]) > float(settings.get("schedule.catchup_hours", 10)) * 3600


def mcp_config(db_path: Path, run_id: int, kind: str, query: str) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {"mcpServers": {"svodka": {
        "command": sys.executable,
        "args": ["-m", "svodka", "mcp"],
        "env": {"SVODKA_DB": str(db_path), "SVODKA_RUN_ID": str(run_id), "SVODKA_RUN_KIND": kind,
                "SVODKA_QUERY": query, "PYTHONPATH": str(PROJECT_ROOT)},
    }}}
    path = RUNTIME_DIR / f"mcp-{run_id}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def build_args(settings, mcp_path: Path, prompt_file: Path) -> list[str]:
    # Свой короткий системный промпт вместо большого промпта Claude Code: сбору не нужны
    # правила работы с кодом, а лимиты Pro тратятся заметно меньше.
    return [
        "--system-prompt-file", str(prompt_file),
        "--model", settings.get("claude.model", "sonnet"),
        "--fallback-model", settings.get("claude.fallback_model", "haiku"),
        "--mcp-config", str(mcp_path), "--strict-mcp-config",
        "--tools", "WebSearch,WebFetch",
        "--allowedTools", "WebSearch,WebFetch,mcp__svodka__*",
        "--permission-mode", "dontAsk",
        "--max-turns", str(int(settings.get("collect.max_turns", 80))),
        "--no-session-persistence",
    ]


def run(settings, storage, kind: str = "collect", query: str = "", force: bool = False,
        on_progress: Callable[[str], None] | None = None, cancel=None,
        db_path: Path | None = None) -> dict:
    """Один запуск сбора/поиска. Возвращает {'status', 'saved', 'message', ...}."""
    ensure_dirs()
    db_path = db_path or DB_FILE
    progress = on_progress or (lambda _m: None)
    try:
        with _Lock(LOCK_FILE):
            if kind == "collect" and not force:
                last = storage.last_run("collect")
                if last and time.time() - float(last["started"]) < RECENT_SKIP_HOURS * 3600:
                    return {"status": "skipped", "saved": 0, "message": "Недавно уже собирали."}
            return _run_locked(settings, storage, kind, query, progress, cancel, db_path)
    except Busy:
        return {"status": "busy", "saved": 0, "message": "Сбор уже идёт."}


def _run_locked(settings, storage, kind, query, progress, cancel, db_path) -> dict:
    run_id = storage.start_run(kind, query)
    prompt_file = PROMPTS_DIR / ("search.md" if kind == "search" else "collect.md")
    if kind == "search":
        system_file = RUNTIME_DIR / f"search-{run_id}.md"
        system_file.parent.mkdir(parents=True, exist_ok=True)
        system_file.write_text(prompt_file.read_text(encoding="utf-8").replace("{query}", query), encoding="utf-8")
        prompt = f"Найди материалы по запросу читателя: «{query}». Начни с get_brief."
    else:
        system_file = prompt_file
        prompt = f"Сейчас {time.strftime('%d.%m.%Y %H:%M')}. Начни сбор: вызови get_brief и действуй по инструкции."
    mcp_path = mcp_config(db_path, run_id, kind, query)
    progress("Запускаю Claude…" if kind == "collect" else f"Ищу в интернете: {query}")
    error, cost = None, 0.0
    try:
        result = claude_cli.run(prompt, build_args(settings, mcp_path, system_file),
                                command=settings.get("claude.command", "claude"),
                                on_progress=progress, cwd=str(RUNTIME_DIR), cancel=cancel,
                                timeout=float(settings.get("collect.timeout_min", 25)) * 60)
        cost = result.cost_usd
    except claude_cli.ClaudeError as exc:
        error = exc
        cost = getattr(getattr(exc, "result", None), "cost_usd", 0.0) or 0.0
    finally:
        for tmp in (mcp_path, system_file if kind == "search" else None):
            try:
                if tmp:
                    tmp.unlink()
            except OSError:
                pass
    saved = int(storage.one("SELECT count(*) AS n FROM articles WHERE run_id=?", (run_id,))["n"])
    if error is None:
        status = "ok" if saved else "empty"
        message = f"Собрано: {saved}" if saved else "Ничего нового не нашлось."
    else:
        status = "partial" if saved else "failed"
        message = error.human() + (f" Успели сохранить: {saved}." if saved else "")
    storage.finish_run(run_id, status, saved=saved, cost_usd=cost,
                       error=(f"{error.kind}: {error.message[:500]}" if error else ""))
    if kind == "search":
        storage.remember_query(query)
        storage.execute("UPDATE queries SET last_checked=? WHERE text=?", (time.time(), query))
    out = {"status": status, "saved": saved, "message": message, "run_id": run_id, "cost_usd": cost,
           "error_kind": error.kind if error else ""}
    if saved:
        _after(settings, storage, kind, progress)
    return out


def _after(settings, storage, kind: str, progress) -> None:
    """После сбора: обслуживание базы и перевод лучших статей заранее."""
    try:
        storage.purge(int(settings.get("storage.keep_days", 30)))
        storage.backup(BACKUP_DIR, int(settings.get("storage.backups", 3)))
    except Exception:  # noqa: BLE001
        log.exception("обслуживание базы")
    n = int(settings.get("translate.prefetch_top", 5)) if kind == "collect" else 3
    if n > 0:
        try:
            from . import translate
            translate.prefetch(settings, storage, n, on_progress=progress)
        except Exception:  # noqa: BLE001 — перевод заранее необязателен
            log.exception("предзагрузка перевода")


def notify(title: str, body: str) -> None:
    """Уведомление рабочего стола (если окна нет — его покажет само приложение)."""
    exe = shutil.which("notify-send")
    if not exe:
        return
    try:
        subprocess.run([exe, "-a", "Сводка", "-i", "news", title, body], timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
