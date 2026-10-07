"""«Разговор»: собеседник, которому можно выговориться.

Отличия от учёбы — намеренные:
- никаких крючков, серий, наград и открытых петель: разговор заканчивается завершённостью,
  а приложение не стремится, чтобы разговоров было больше;
- каждый разговор — новый разговор Claude; непрерывность даёт только «память» — короткие
  сводки, которые человек сам разрешил запомнить;
- переписка по умолчанию не хранится: ни в базе, ни в журнале Claude Code (файл удаляется
  после разговора);
- учёба видит из разговоров только одно — был ли недавно тяжёлый момент (самочувствие ≤ 3).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from . import claude_cli
from .config import CLAUDE_TALK_DIR, PROMPTS_DIR, ensure_dirs
from .util import now

log = logging.getLogger(__name__)

MODES = {"listen": "Просто выслушай", "understand": "Помоги разобраться", "act": "Что делать"}
MODE_HINTS = {"listen": "Без советов: отражаю и помогаю назвать чувства",
              "understand": "Вопросы по одному, поиск закономерностей",
              "act": "Варианты и один маленький шаг"}
FEELINGS = ["тревога", "злость", "обида", "грусть", "усталость", "растерянность", "одиночество", "пусто",
            "радость", "спокойно"]
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "helped": {"type": "string"},
        "technique": {"type": "string"},
    },
    "required": ["summary", "themes"],
}


def start(storage, settings, mode: str, mood_before: int | None, feeling: str = "") -> int:
    mode = mode if mode in MODES else "listen"
    return storage.start_session("talk", None, started=now(), mode=mode, mood_before=mood_before,
                                 feeling=feeling[:60])


def memory_block(storage, settings, limit: int = 5) -> str:
    if settings.get("talk.memory", "ask") == "never":
        return ""
    notes = storage.talk_notes(limit)
    if not notes:
        return ""
    lines = ["[память] Из прошлых разговоров (человек разрешил помнить):"]
    for n in reversed(notes):
        extra = f" Помогло: {n['helped']}." if n["helped"] else ""
        lines.append(f"- {n['summary']}{extra}")
    return "\n".join(lines)


def _args(settings, resume: str | None, schema: dict | None = None) -> list[str]:
    args = ["--system-prompt-file", str(PROMPTS_DIR / "listener.md"),
            "--model", settings.get("claude.model", "sonnet"),
            "--fallback-model", settings.get("claude.fallback_model", "haiku"),
            "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--permission-mode", "dontAsk", "--max-turns", "3"]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema)]
    else:
        args.append("--include-partial-messages")
    if resume:
        args += ["--resume", resume]
    return args


def send(storage, settings, session_id: int, text: str, on_text: Callable[[str], None] | None = None,
         on_progress: Callable[[str], None] | None = None, cancel: threading.Event | None = None,
         mode_changed: bool = False) -> dict:
    ensure_dirs()
    s = storage.session(session_id)
    keep = bool(settings.get("talk.keep_transcripts", False))
    if keep:
        storage.add_message(session_id, "user", text)
    resume = s["claude_session"] or None
    prefix = []
    if not resume:
        mood = f"{s['mood_before']}/10" if s["mood_before"] is not None else "не указано"
        prefix.append(f"[приложение] Режим: {s['mode']} ({MODES.get(s['mode'], '')}). "
                      f"Самочувствие в начале: {mood}." + (f" Чувство: {s['feeling']}." if s["feeling"] else ""))
        mem = memory_block(storage, settings)
        if mem:
            prefix.append(mem)
    elif mode_changed:
        prefix.append(f"[приложение] Человек сменил режим: {s['mode']} ({MODES.get(s['mode'], '')}).")
    prompt = "\n\n".join(prefix + [text])
    chunks: list[str] = []

    def collect(piece: str) -> None:
        chunks.append(piece)
        if on_text:
            on_text(piece)
    res = claude_cli.run(prompt, _args(settings, resume), command=settings.get("claude.command", "claude"),
                         on_text=collect, on_progress=on_progress,
                         timeout=float(settings.get("claude.timeout_s", 300)), cwd=str(CLAUDE_TALK_DIR),
                         cancel=cancel)
    answer = "".join(chunks).strip() or (res.text or "").strip()
    if res.session_id and res.session_id != s["claude_session"]:
        storage.update_session(session_id, claude_session=res.session_id)
    if keep:
        storage.add_message(session_id, "assistant", answer)
    return {"text": answer}


def set_mode(storage, session_id: int, mode: str) -> None:
    if mode in MODES:
        storage.update_session(session_id, mode=mode)


def closing(storage, settings, session_id: int, on_text=None, cancel=None) -> dict:
    """Короткое завершение от собеседника: что стало яснее."""
    s = storage.session(session_id)
    if not s["claude_session"]:
        return {"text": ""}
    return send(storage, settings, session_id, "[приложение] завершение", on_text=on_text, cancel=cancel)


def summarize(storage, settings, session_id: int, cancel: threading.Event | None = None) -> dict | None:
    """Сводка для памяти (строгий JSON). Человек увидит её и сам решит, запомнить ли."""
    s = storage.session(session_id)
    if not s or not s["claude_session"] or settings.get("talk.memory", "ask") == "never":
        return None
    prompt = (PROMPTS_DIR / "talk_summary.md").read_text(encoding="utf-8")
    try:
        res = claude_cli.run(prompt, _args(settings, s["claude_session"], SUMMARY_SCHEMA),
                             command=settings.get("claude.command", "claude"),
                             timeout=float(settings.get("claude.timeout_s", 300)), cwd=str(CLAUDE_TALK_DIR),
                             cancel=cancel)
    except claude_cli.ClaudeError as exc:
        log.warning("Сводка разговора не получилась: %s", exc.message)
        return None
    out = res.structured if isinstance(res.structured, dict) else None
    if not out or not str(out.get("summary", "")).strip():
        return None
    return {"summary": str(out["summary"]).strip(),
            "themes": [str(t).strip() for t in out.get("themes") or [] if str(t).strip()][:4],
            "helped": str(out.get("helped", "")).strip(), "technique": str(out.get("technique", "")).strip()}


def finish(storage, settings, session_id: int, mood_after: int | None, note: dict | None,
           active_ms: int = 0) -> dict:
    """Закрыть разговор: самочувствие после, сводка (если человек согласился), чистка переписки."""
    s = storage.session(session_id)
    storage.update_session(session_id, ended=now(), mood_after=mood_after, active_ms=int(active_ms))
    if note:
        storage.add_talk_note(session_id, note["summary"], note.get("themes") or [], note.get("helped", ""),
                              note.get("technique", ""))
    deleted = False
    if not settings.get("talk.keep_transcripts", False):
        storage.delete_messages(session_id)
        if s and s["claude_session"]:
            deleted = delete_transcript(s["claude_session"])
    delta = (mood_after - s["mood_before"]) if (mood_after is not None and s and s["mood_before"] is not None) else None
    return {"delta": delta, "transcript_deleted": deleted}


def delete_transcript(claude_session: str) -> bool:
    """Удалить журнал разговора, который Claude Code хранит в ~/.claude/projects/…/<id>.jsonl."""
    if not claude_session or "/" in claude_session or ".." in claude_session:
        return False
    removed = False
    base = Path.home() / ".claude" / "projects"
    for path in base.glob(f"*/{claude_session}.jsonl"):
        try:
            path.unlink()
            removed = True
        except OSError:
            pass
    for path in base.glob(f"*/{claude_session}"):           # папка с вложениями разговора, если есть
        if path.is_dir():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
    return removed
