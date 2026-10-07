"""Проверка ответа на карточку: Claude сравнивает ответ с эталоном, ставит оценку 1–4 и коротко объясняет.

Оценка идёт прямо в FSRS — человек себе ничего не выставляет. Пустой ответ («не помню»)
не отправляется: это «не вспомнил» без вопросов к Claude. Схему с доски Claude получает в Mermaid
(и картинкой, если на ней есть подписи от руки).

Проверка — отдельный короткий запуск `claude -p` без инструментов и без истории: быстрая модель
(Haiku по умолчанию, Настройки → Claude), строгий JSON по схеме.
"""
from __future__ import annotations

import json
import threading

from . import claude_cli
from .config import CLAUDE_LEARN_DIR, PROMPTS_DIR, ensure_dirs

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "integer", "minimum": 1, "maximum": 4},
        "explanation": {"type": "string"},
    },
    "required": ["grade", "explanation"],
}
VERDICTS = {1: "Не вспомнил", 2: "Частично", 3: "Верно", 4: "Верно и легко"}
NO_ANSWER = "Ответа нет — считаю как «не вспомнил». Карточка вернётся скоро."


def check_answer(settings, item: dict, answer: str, seconds: float = 0.0, png: bytes | None = None,
                 cancel: threading.Event | None = None) -> dict:
    """{'grade': 1–4, 'explanation': str}. Бросает ClaudeError с понятной причиной."""
    answer = (answer or "").strip()
    if not answer:
        return {"grade": 1, "explanation": NO_ANSWER}
    ensure_dirs()
    data = {
        "вопрос": item.get("prompt", ""),
        "эталон": (item.get("answer") or "").strip() or "(эталона нет — оцени по сути вопроса)",
        "вид": "схема с доски (Mermaid)" if item.get("kind") == "schema" else "карточка",
        "ответ человека": answer,
        "время на ответ, секунд": round(seconds),
    }
    model = settings.get("claude.review_model", "haiku")
    fallback = settings.get("claude.fallback_model", "haiku")
    args = ["--system-prompt-file", str(PROMPTS_DIR / "review_check.md"), "--model", model,
            "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--json-schema", json.dumps(CHECK_SCHEMA), "--permission-mode", "dontAsk",
            "--max-turns", "3", "--no-session-persistence"]
    if fallback and fallback != model:
        args += ["--fallback-model", fallback]
    prompt = "Проверь ответ на карточку.\n\n" + json.dumps(data, ensure_ascii=False, indent=1)
    res = claude_cli.run(prompt, args, command=settings.get("claude.command", "claude"),
                         timeout=min(180.0, float(settings.get("claude.timeout_s", 300))),
                         cwd=str(CLAUDE_LEARN_DIR), cancel=cancel, images=[png] if png else None)
    out = res.structured if isinstance(res.structured, dict) else _json_from_text(res.text)
    try:
        grade = max(1, min(4, int((out or {}).get("grade"))))
    except (TypeError, ValueError):
        raise claude_cli.ClaudeError("failed", "Claude не вернул оценку") from None
    explanation = " ".join(str(out.get("explanation") or "").split())[:400]
    return {"grade": grade, "explanation": explanation}


def _json_from_text(text: str) -> dict | None:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None
