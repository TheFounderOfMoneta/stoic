"""Необязательная ИИ-обработка через OpenAI-совместимый API (Ollama, LM Studio, llama.cpp…).

Используется для «Инструкций» (стиль текста под приложение) и «Командного
режима» (изменить выделенный текст голосом). Без настроенного сервера
приложение полностью работает и так — GigaAM уже расставляет пунктуацию.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

DICTATION_SYSTEM = (
    "Ты — редактор диктовки. Тебе дают распознанную речь. Верни ТОЛЬКО итоговый текст для вставки, "
    "без пояснений и кавычек. Сохраняй смысл и язык автора. Исправляй явные ошибки распознавания, "
    "учитывай самопоправки («нет, точнее…» — оставь только исправленный вариант), убирай слова-паразиты, "
    "оформляй перечисления списками, если автор явно перечисляет пункты. Ничего не добавляй от себя."
)
COMMAND_SYSTEM = (
    "Ты — помощник по редактированию текста. Пользователь голосом даёт команду. Если передан выделенный "
    "текст, примени команду к нему и верни ТОЛЬКО новый вариант текста. Если выделенного текста нет, "
    "выполни команду и верни ТОЛЬКО готовый текст для вставки. Без пояснений, без markdown-ограждений."
)


class LLMError(RuntimeError):
    pass


def chat(settings, system: str, user: str) -> str:
    base = (settings.get("llm.base_url") or "").rstrip("/")
    if not base:
        raise LLMError("Не указан адрес API")
    payload = {
        "model": settings.get("llm.model") or "",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    key = settings.get("llm.api_key")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=float(settings.get("llm.timeout_s") or 12)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неожиданный ответ: {str(body)[:200]}") from exc
    return _strip_fences(text).strip()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    # Модели-«размышлялки» (qwen3, deepseek-r1) присылают <think>…</think>
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text


def _app_style(settings, app: str) -> str:
    styles = settings.get("llm.app_styles") or {}
    app_l = (app or "").lower()
    for key, style in styles.items():
        if key and key.lower() in app_l:
            return style
    return ""


def polish_dictation(settings, text: str, app: str, title: str, vocabulary: list[str]) -> str:
    parts = []
    instructions = (settings.get("llm.instructions") or "").strip()
    if instructions:
        parts.append(f"Инструкции пользователя:\n{instructions}")
    style = _app_style(settings, app)
    if style:
        parts.append(f"Стиль для этого приложения:\n{style}")
    if settings.get("llm.use_context", True) and (app or title):
        parts.append(f"Текст вставляется в приложение «{app}», окно «{title}».")
    if vocabulary:
        parts.append("Словарь (пиши эти слова именно так): " + ", ".join(vocabulary[:200]))
    parts.append(f"Распознанная речь:\n{text}")
    return chat(settings, DICTATION_SYSTEM, "\n\n".join(parts))


def run_command(settings, command: str, selection: str, app: str, title: str) -> str:
    parts = [f"Команда: {command}"]
    if selection.strip():
        parts.append(f"Выделенный текст:\n<<<\n{selection}\n>>>")
    if settings.get("llm.use_context", True) and (app or title):
        parts.append(f"Приложение: «{app}», окно «{title}».")
    instructions = (settings.get("llm.instructions") or "").strip()
    if instructions:
        parts.append(f"Общие предпочтения пользователя:\n{instructions}")
    return chat(settings, COMMAND_SYSTEM, "\n\n".join(parts))


def list_models(settings) -> list[str]:
    base = (settings.get("llm.base_url") or "").rstrip("/")
    req = urllib.request.Request(f"{base}/models")
    key = settings.get("llm.api_key")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=4) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
