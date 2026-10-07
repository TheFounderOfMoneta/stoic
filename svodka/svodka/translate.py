"""Полный перевод статьи на русский — блоками, потоком, с проверкой.

- Облегчённый вызов Claude: свой короткий системный промпт и никаких инструментов —
  большой промпт Claude Code и инструменты не тратят лимиты Pro.
- Блоки идут с номерами; ответ — по строке JSON на блок, поэтому Читалка показывает
  перевод абзац за абзацем, пока он генерируется.
- Проверка: все номера на месте, цифры не потерялись. Пропущенное переводится повторно.
- Длинные статьи — частями (~1500 слов); словарь терминов читателя применяется всегда.
- Перевод хранится, пока хранится статья, и не делается дважды.
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

TRANSLATABLE = ("p", "h2", "h3", "li", "quote", "table")
_DIGITS = re.compile(r"\d+")
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _article_lock(article_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(article_id, threading.Lock())


def needs_translation(article: dict) -> bool:
    return (article.get("lang") or "en") != "ru" and article.get("extract_status") == "ok" \
        and article.get("translate_status") not in ("done", "not_needed")


def check_block(orig: str, ru: str) -> bool:
    """Перевод правдоподобен: не пустой, числа на месте, длина в разумных пределах.

    Проверяем числа из 2–4 цифр (годы, проценты, суммы): их нельзя потерять. Однозначные могут
    быть написаны словами, а большие — переформатированы («1,000,000» → «1 млн»).
    """
    if not ru or not ru.strip():
        return False
    letters = sum(ch.isalpha() for ch in orig or "")
    if len(orig or "") < 60 and letters < len(orig or "") * 0.35:
        return True             # строка-дата или цифры: модель могла переписать её по-русски
    want = {n.lstrip("0") for n in _DIGITS.findall(orig or "") if 2 <= len(n) <= 4} - {""}
    compact = ru.replace(chr(0xA0), "").replace(" ", "")
    have = {n.lstrip("0") for n in _DIGITS.findall(compact)}
    if want - have:
        return False
    ratio = len(ru) / max(1, len(orig))
    return 0.35 <= ratio <= 3.5 or len(orig) < 40


def chunks(blocks: list[dict], max_words: int) -> list[list[dict]]:
    out, cur, n = [], [], 0
    for b in blocks:
        w = words_count(b["text_orig"])
        if cur and n + w > max_words:
            out.append(cur)
            cur, n = [], 0
        cur.append(b)
        n += w
    if cur:
        out.append(cur)
    return out


class _LineParser:
    """Собирает поток текста в строки JSON {"id", "ru"}."""

    def __init__(self, on_item: Callable[[int, str], None]):
        self.buf = ""
        self.on_item = on_item

    def feed(self, text: str) -> None:
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line)

    def flush(self) -> None:
        if self.buf.strip():
            self._line(self.buf)
        self.buf = ""

    def _line(self, line: str) -> None:
        line = line.strip().strip(",")
        if not line.startswith("{"):
            return
        try:
            item = json.loads(line)
        except ValueError:
            return
        if isinstance(item, dict) and isinstance(item.get("ru"), str):
            try:
                self.on_item(int(item.get("id")), item["ru"])
            except (TypeError, ValueError):
                pass


def translate_article(settings, storage, article_id: int, on_block: Callable[[int, str], None] | None = None,
                      on_progress: Callable[[str], None] | None = None, cancel=None) -> dict:
    """Перевести недостающие блоки статьи. {'status': done|partial|skipped|failed, 'message'}."""
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
    for b in blocks:                       # код и картинки не переводятся
        if b["type"] not in TRANSLATABLE and not b["text_ru"]:
            storage.set_block_ru(article_id, b["idx"], b["text_orig"])
    todo = [b for b in blocks if b["type"] in TRANSLATABLE and not b["text_ru"]]
    if not todo:
        storage.update_article(article_id, translate_status="done")
        return {"status": "done", "message": "Перевод готов."}
    ensure_dirs()
    glossary = storage.glossary()
    total = len(todo)
    done = 0
    error: claude_cli.ClaudeError | None = None
    for attempt in range(2):
        for part in chunks(todo, int(settings.get("translate.chunk_words", 1500))):
            by_id = {b["idx"]: b for b in part}

            def accept(idx: int, ru: str, by_id=by_id) -> None:
                nonlocal done
                b = by_id.get(idx)
                if b is None or b.get("_done"):
                    return
                if not check_block(b["text_orig"], ru):
                    return
                b["_done"] = True
                storage.set_block_ru(article_id, idx, ru.strip())
                done += 1
                if on_block:
                    on_block(idx, ru.strip())
                if on_progress:
                    on_progress(f"Переводится… {done} из {total}")

            payload = {"заголовок": article.get("title_orig") or article.get("title_ru"),
                       "источник": article.get("source", ""), "словарь терминов": glossary,
                       "блоки": [{"id": b["idx"], "type": b["type"], "text": b["text_orig"]} for b in part]}
            parser = _LineParser(accept)
            try:
                res = claude_cli.run(
                    "Переведи блоки статьи из входных данных по правилам. Только строки JSON.",
                    ["--system-prompt-file", str(PROMPTS_DIR / "translate.md"), "--tools", "",
                     "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--model", settings.get("translate.model", "sonnet"),
                     "--include-partial-messages", "--max-turns", "2", "--no-session-persistence"],
                    command=settings.get("claude.command", "claude"),
                    stdin=json.dumps(payload, ensure_ascii=False), on_text=parser.feed, cwd=str(RUNTIME_DIR),
                    timeout=600, cancel=cancel)
                parser.flush()
                for line in (res.text or "").splitlines():    # на случай, если поток не пришёл
                    parser._line(line)
            except claude_cli.ClaudeError as exc:
                log.warning("перевод статьи %s: %s — %s", article_id, exc.kind, exc.message[:300])
                error = exc
                break
        todo = [b for b in todo if not b.get("_done")]
        if not todo or error is not None:
            break
    left = len([b for b in storage.blocks(article_id) if b["type"] in TRANSLATABLE and not b["text_ru"]])
    status = "done" if left == 0 else ("partial" if done or total > left else "none")
    storage.update_article(article_id, translate_status=status)
    search.reindex(storage, article_id)
    if error is not None and status != "done":
        return {"status": "failed" if not done else "partial", "message": error.human(), "error_kind": error.kind}
    return {"status": status, "message": "Перевод готов." if status == "done" else f"Не переведено блоков: {left}."}


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
