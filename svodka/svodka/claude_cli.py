"""Обёртка над `claude -p` (Claude Code по подписке Pro).

Запускает Claude неинтерактивно, читает поток событий (stream-json), показывает понятный
прогресс («Ищу: …», «Читаю: …») и превращает сбои в понятные причины:
вход истёк, кончился лимит, нет сети, Claude не установлен.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

AUTH_MARKERS = ("invalid api key", "please run /login", "authentication_failed", "oauth token has expired",
                "not logged in", "login expired", "run /login", "credentials", "unauthorized", "401")
LIMIT_MARKERS = ("usage limit", "rate limit", "rate_limit", "limit reached", "limit will reset", "resets at",
                 "out of extra usage", "429", "quota")
NETWORK_MARKERS = ("econnrefused", "enotfound", "getaddrinfo", "network", "connection error", "etimedout",
                   "socket hang up", "unable to connect", "dns")


class ClaudeError(Exception):
    """kind: not_installed | auth | limit | network | timeout | failed."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    def human(self) -> str:
        return {
            "not_installed": "Claude Code не установлен. Установите его командой из инструкции и войдите в аккаунт.",
            "auth": "Вход в Claude истёк или не выполнен. Нажмите «Войти в Claude».",
            "limit": "Лимит подписки Claude на время исчерпан. Продолжу сам, когда он обновится.",
            "network": "Нет связи с Claude. Повторю, когда появится сеть.",
            "timeout": "Claude слишком долго не отвечал — запуск остановлен.",
        }.get(self.kind, f"Claude не справился: {self.message[:200]}")


@dataclass
class ClaudeResult:
    text: str = ""
    structured: Optional[dict] = None
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0
    is_error: bool = False
    subtype: str = ""
    denials: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)


def classify(text: str) -> str:
    low = (text or "").lower()
    if any(m in low for m in LIMIT_MARKERS):
        return "limit"
    if any(m in low for m in AUTH_MARKERS):
        return "auth"
    if any(m in low for m in NETWORK_MARKERS):
        return "network"
    return "failed"


def find_claude(command: str = "claude") -> str | None:
    """Путь к claude: из настроек, PATH или стандартных мест установки."""
    if command and os.path.isabs(command) and os.access(command, os.X_OK):
        return command
    found = shutil.which(command or "claude")
    if found:
        return found
    home = Path.home()
    for cand in (home / ".local/bin/claude", home / ".claude/local/claude", Path("/usr/local/bin/claude"),
                 Path("/usr/bin/claude")):
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def auth_status(command: str = "claude", timeout: float = 20) -> dict:
    """{'ok': bool, 'installed': bool, 'method': str, 'message': str}."""
    exe = find_claude(command)
    if not exe:
        return {"ok": False, "installed": False, "method": "", "message": ClaudeError("not_installed", "").human()}
    try:
        out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout or "{}")
        ok = bool(data.get("loggedIn"))
        return {"ok": ok, "installed": True, "method": data.get("authMethod", ""),
                "message": "" if ok else ClaudeError("auth", "").human()}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"ok": False, "installed": True, "method": "", "message": f"Не удалось проверить вход: {exc}"}


def describe_tool(name: str, inp: dict) -> str:
    """Человеческое описание шага для строки прогресса."""
    inp = inp or {}
    if name == "WebSearch":
        return f"Ищу: {inp.get('query', '')}"
    if name == "WebFetch":
        return f"Читаю: {urlsplit(inp.get('url', '')).netloc}"
    short = name.replace("mcp__svodka__", "")
    if short == "read_article":
        return f"Читаю: {urlsplit(inp.get('url', '')).netloc}"
    if short == "save_articles":
        return f"Сохраняю статьи: {len(inp.get('items') or [])}"
    if short == "get_brief":
        return "Смотрю, что вам интересно"
    if short == "filter_new":
        return "Отсеиваю уже знакомое"
    if short == "finish_run":
        return "Заканчиваю"
    return short


def run(prompt: str, args: list[str], command: str = "claude", stdin: str | None = None,
        on_event: Callable[[dict], None] | None = None, on_progress: Callable[[str], None] | None = None,
        on_text: Callable[[str], None] | None = None, timeout: float = 1500, cwd: str | None = None,
        env: dict | None = None, cancel: threading.Event | None = None) -> ClaudeResult:
    """Запустить `claude -p` и дождаться результата. Бросает ClaudeError с понятной причиной.

    prompt — задача (передаётся аргументом); stdin — данные (например, блоки текста для перевода).
    on_text получает куски текста по мере генерации (нужен --include-partial-messages).
    """
    exe = find_claude(command)
    if not exe:
        raise ClaudeError("not_installed", "claude not found")
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose", *args]
    full_env = dict(os.environ)
    # Ключ API в окружении перебил бы вход по подписке — убираем, чтобы тратилась подписка Pro.
    full_env.pop("ANTHROPIC_API_KEY", None)
    full_env.update(env or {})
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
                                env=full_env, bufsize=1)
    except OSError as exc:
        raise ClaudeError("not_installed", str(exc)) from exc
    if stdin is not None:
        def feed():
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=feed, daemon=True).start()
    stderr_lines: list[str] = []

    def read_err():
        for line in proc.stderr:
            stderr_lines.append(line)
    threading.Thread(target=read_err, daemon=True).start()

    killer_done = threading.Event()

    def watchdog():
        while not killer_done.wait(1.0):
            if time.time() - started > timeout or (cancel is not None and cancel.is_set()):
                try:
                    proc.terminate()
                    time.sleep(3)
                    if proc.poll() is None:
                        proc.kill()
                except OSError:
                    pass
                return
    threading.Thread(target=watchdog, daemon=True).start()

    result = ClaudeResult()
    final: dict | None = None
    retry_note = ""
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if on_event:
                try:
                    on_event(ev)
                except Exception:  # noqa: BLE001
                    log.exception("on_event")
            et = ev.get("type")
            if et == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        result.tools_used.append(block.get("name", ""))
                        if on_progress:
                            on_progress(describe_tool(block.get("name", ""), block.get("input") or {}))
            elif et == "stream_event" and on_text:
                delta = ((ev.get("event") or {}).get("delta") or {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    on_text(delta["text"])
            elif et == "system" and ev.get("subtype") == "api_retry":
                retry_note = str(ev.get("error") or "")
                if on_progress:
                    on_progress(f"Claude занят, повторяю (попытка {ev.get('attempt')})…")
            elif et == "result":
                final = ev
    finally:
        proc.wait()
        killer_done.set()
    result.duration_s = time.time() - started
    if cancel is not None and cancel.is_set():
        raise ClaudeError("failed", "отменено")
    if final is None:
        err = "".join(stderr_lines)[-2000:]
        if time.time() - started > timeout:
            raise ClaudeError("timeout", err or "timeout")
        raise ClaudeError(classify(err + " " + retry_note), err or f"claude завершился с кодом {proc.returncode}")
    result.text = final.get("result") or ""
    result.structured = final.get("structured_output")
    result.cost_usd = float(final.get("total_cost_usd") or 0.0)
    result.turns = int(final.get("num_turns") or 0)
    result.subtype = final.get("subtype") or ""
    result.is_error = bool(final.get("is_error"))
    result.denials = final.get("permission_denials") or []
    if result.is_error:
        text = result.text or "".join(stderr_lines)[-1000:] or result.subtype
        kind = classify(text + " " + retry_note)
        if result.subtype == "error_max_turns":
            kind = "failed"
        err = ClaudeError(kind, text)
        err.result = result  # type: ignore[attr-defined]
        raise err
    return result
