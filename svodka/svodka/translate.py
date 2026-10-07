"""Статья по-русски — одним вызовом Claude, как её написал бы русский журналист.

Не подстрочник «абзац в абзац», а редакторский перевод: естественный русский без калек и
американских оборотов, без служебного мусора сайтов и рассылок, все факты, цифры и имена на месте.
Длинные материалы (больше ~2500 слов) — сжатым пересказом: читать быстрее, лимитов уходит меньше.

Экономия лимитов подписки:
- один вызов на статью (не частями), свой короткий системный промпт, без инструментов;
- ответ — простой текст, а не JSON (меньше выходных токенов — они самые дорогие);
- код и картинки не пересылаются: в тексте только метки [[img 3]] / [[code 5]].
Текст приходит потоком — Читалка показывает абзацы по мере готовности.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Callable

from . import claude_cli, search
from .config import PROMPTS_DIR, RUNTIME_DIR, ensure_dirs
from .util import words_count

log = logging.getLogger(__name__)

TEXT_TYPES = ("p", "h2", "h3", "li", "quote", "table")
LONG_WORDS = 2500           # длиннее — сжатый пересказ
_MARK = re.compile(r"^\[\[(img|code) (\d+)\]\]$")
_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _article_lock(article_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(article_id, threading.Lock())


def needs_translation(article: dict) -> bool:
    return (article.get("lang") or "en") != "ru" and article.get("extract_status") == "ok" \
        and article.get("translate_status") not in ("done", "not_needed")


def is_condensed(article: dict) -> bool:
    return int(article.get("words") or 0) > LONG_WORDS


def source_text(blocks: list[dict]) -> str:
    """Оригинал для Claude: простая разметка, код и картинки — метками."""
    out = []
    for b in blocks:
        t, text = b["type"], (b.get("text_orig") or b.get("text") or "").strip()
        if t == "img":
            if b.get("src"):
                out.append(f"[[img {b['idx']}]]")
        elif t == "code":
            out.append(f"[[code {b['idx']}]]")
        elif not text:
            continue
        elif t == "h2":
            out.append("## " + text)
        elif t == "h3":
            out.append("### " + text)
        elif t == "li":
            out.append("- " + text)
        elif t == "quote":
            out.append("> " + text)
        elif t == "table":
            out.append("\n".join("| " + row + " |" for row in text.split("\n")))
        else:
            out.append(text)
    return "\n\n".join(out)


class Builder:
    """Поток текста от Claude → блоки русского текста (по одной строке на абзац)."""

    def __init__(self, originals: dict[int, dict], emit: Callable[[dict], None]):
        self.originals = originals
        self.emit = emit
        self.buf = ""
        self.table: list[str] = []
        self.count = 0
        self.text_chars = 0
        self.cyr_chars = 0
        self.used_marks: set[int] = set()

    def feed(self, text: str) -> None:
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line)

    def flush(self) -> None:
        if self.buf.strip():
            self._line(self.buf)
        self.buf = ""
        self._flush_table()

    def _out(self, kind: str, text: str, src: str | None = None) -> None:
        if kind not in ("img", "code"):
            self.text_chars += len(_CYR.findall(text)) + len(_LAT.findall(text))
            self.cyr_chars += len(_CYR.findall(text))
        self.emit({"idx": self.count, "type": kind, "text": text, "src": src})
        self.count += 1

    def _flush_table(self) -> None:
        if self.table:
            self._out("table", "\n".join(self.table))
            self.table = []

    def _line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):       # строка-разделитель markdown
                self.table.append(" | ".join(cells))
            return
        self._flush_table()
        m = _MARK.match(line)
        if m:
            idx = int(m.group(2))
            orig = self.originals.get(idx)
            if orig is not None and idx not in self.used_marks:
                self.used_marks.add(idx)
                self._out(orig["type"], orig.get("text_orig") or "", orig.get("src"))
            return
        if line.startswith("### "):
            self._out("h3", line[4:].strip())
        elif line.startswith("## ") or line.startswith("# "):
            self._out("h2", line.lstrip("#").strip())
        elif line[:2] in ("- ", "• ", "* "):
            self._out("li", line[2:].strip())
        elif line.startswith(">"):
            self._out("quote", line.lstrip("> ").strip())
        else:
            self._out("p", line.replace("**", ""))

    def russian(self) -> bool:
        return self.count > 0 and self.cyr_chars >= 0.4 * max(1, self.text_chars)


def translate_article(settings, storage, article_id: int, on_block: Callable[[dict], None] | None = None,
                      on_progress: Callable[[str], None] | None = None, cancel=None) -> dict:
    """Статья по-русски. {'status': done|partial|failed|skipped|busy, 'message', 'error_kind'?}."""
    lock = _article_lock(article_id)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "message": "Перевод уже идёт."}
    try:
        return _translate(settings, storage, article_id, on_block, on_progress, cancel)
    finally:
        lock.release()


def _translate(settings, storage, article_id, on_block, on_progress, cancel) -> dict:
    article = storage.article(article_id)
    if not article:
        return {"status": "failed", "message": "Статья не найдена."}
    if (article.get("lang") or "") == "ru":
        storage.update_article(article_id, translate_status="not_needed")
        return {"status": "skipped", "message": "Статья на русском."}
    blocks = storage.blocks(article_id)
    if not any(b["type"] in TEXT_TYPES and b["text_orig"].strip() for b in blocks):
        return {"status": "failed", "message": "Текста статьи нет."}
    ensure_dirs()
    words = sum(words_count(b["text_orig"]) for b in blocks if b["type"] in TEXT_TYPES)
    condensed = words > LONG_WORDS
    payload = {"заголовок": article.get("title_orig") or article.get("title_ru"),
               "источник": article.get("source") or article.get("domain", ""),
               "режим": "сжатый пересказ" if condensed else "полный перевод",
               "словарь терминов": storage.glossary(), "текст": source_text(blocks)}
    storage.clear_ru_blocks(article_id)

    def emit(b: dict) -> None:
        storage.add_ru_block(article_id, b["idx"], b["type"], b["text"], b.get("src"))
        if on_block:
            on_block(b)
        if on_progress and b["idx"] % 3 == 0:
            on_progress(f"Переводится… абзацев: {b['idx'] + 1}")

    builder = Builder({b["idx"]: b for b in blocks}, emit)
    error: claude_cli.ClaudeError | None = None
    try:
        res = claude_cli.run(
            "Переведи статью из входных данных по правилам.",
            ["--system-prompt-file", str(PROMPTS_DIR / "translate.md"), "--tools", "",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
             "--model", settings.get("translate.model", "sonnet"),
             "--include-partial-messages", "--max-turns", "2", "--no-session-persistence"],
            command=settings.get("claude.command", "claude"),
            stdin=json.dumps(payload, ensure_ascii=False), on_text=builder.feed, cwd=str(RUNTIME_DIR),
            timeout=900, cancel=cancel)
        builder.flush()
        if builder.count == 0 and res.text:          # поток не пришёл — берём итоговый текст
            builder.feed(res.text + "\n")
            builder.flush()
    except claude_cli.ClaudeError as exc:
        log.warning("перевод статьи %s: %s — %s", article_id, exc.kind, exc.message[:300])
        error = exc
        builder.flush()
    if builder.count and not builder.russian():
        log.warning("перевод статьи %s: ответ не по-русски — отброшен", article_id)
        storage.clear_ru_blocks(article_id)
        storage.update_article(article_id, translate_status="none")
        return {"status": "failed", "message": "Перевод не получился — попробую ещё раз при следующем открытии."}
    if error is not None:
        status = "partial" if builder.count else "none"
        storage.update_article(article_id, translate_status=status)
        return {"status": "partial" if builder.count else "failed", "message": error.human(),
                "error_kind": error.kind}
    if not builder.count:
        storage.update_article(article_id, translate_status="none")
        return {"status": "failed", "message": "Claude не вернул перевод."}
    storage.update_article(article_id, translate_status="done")
    search.reindex(storage, article_id)
    return {"status": "done", "message": "Перевод готов.", "condensed": condensed}


def prefetch(settings, storage, n: int, on_progress: Callable[[str], None] | None = None) -> int:
    """Заранее перевести n самых вероятных для вас статей (по прогнозу ленты)."""
    from .rank.ranker import Ranker
    feed = Ranker(storage, settings).build()
    candidates = [it.article for it in feed.main + feed.more if needs_translation(it.article)][:n]
    count = failures = 0
    for a in candidates:
        if on_progress:
            on_progress(f"Перевожу заранее: {a.get('title_ru', '')[:60]}")
        res = translate_article(settings, storage, a["id"])
        if res["status"] == "done":
            count += 1
            failures = 0
            continue
        log.warning("перевод заранее, статья %s: %s — %s", a["id"], res["status"], res.get("message", ""))
        failures += 1
        # Лимит или вход — дальше бессмысленно; две неудачи подряд — тоже стоп: перевод заранее
        # необязателен, а лимит подписки нужнее для сбора. Остальное переведётся при открытии.
        if res.get("error_kind") in ("limit", "auth", "not_installed") or failures >= 2:
            break
    return count
