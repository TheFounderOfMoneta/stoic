"""Необязательная ИИ-обработка через OpenAI-совместимый API (Ollama, LM Studio, llama.cpp…).

Используется для Edit Mode (изменить выделенный текст голосом) — со встроенной
моделью Qwen3.5-0.8B или со своим сервером (Ollama, LM Studio). Исправление
распознанного текста — в corrector.py.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

COMMAND_SYSTEM = (
    "Ты — помощник по редактированию текста. Пользователь голосом даёт команду. Если передан выделенный "
    "текст, примени команду к нему и верни ТОЛЬКО новый вариант текста. Если выделенного текста нет, "
    "выполни команду и верни ТОЛЬКО готовый текст для вставки. Без пояснений, без markdown-ограждений."
)


class LLMError(RuntimeError):
    pass


# Рекомендации Qwen3.5 для текстовых задач без «размышлений» (профиль пользователя).
SAMPLING = {"temperature": 1.0, "top_p": 1.0, "top_k": 20, "min_p": 0.0, "presence_penalty": 2.0,
            "repeat_penalty": 1.0}
STYLE = ("Отвечай на языке пользователя. По умолчанию отвечай по-русски. Пиши грамотно и естественно. "
         "Строго соблюдай заданный формат, число пунктов и ограничения длины.")


def chat(settings, system: str, user: str, base_url: str | None = None, provider: str = "openai") -> str:
    """Один запрос к модели: builtin (llama-server), ollama (родной API) или openai-совместимый сервер."""
    base = (base_url or settings.get("llm.base_url") or "").rstrip("/")
    if not base:
        raise LLMError("ИИ не подключён")
    messages = [{"role": "system", "content": f"{system}\n{STYLE}"}, {"role": "user", "content": user}]
    if provider == "ollama":
        url = f"{base}/api/chat"
        payload = {"model": settings.get("llm.ollama_model") or "qwen3.5:0.8b-local", "messages": messages,
                   "stream": False, "think": False, "keep_alive": -1,
                   "options": {**SAMPLING, "num_ctx": 4096}}
    else:
        url = f"{base}/chat/completions"
        payload = {"messages": messages, "stream": False, "temperature": SAMPLING["temperature"],
                   "top_p": SAMPLING["top_p"], "presence_penalty": SAMPLING["presence_penalty"]}
        if provider == "builtin":
            payload.update({"model": "qwen", "top_k": SAMPLING["top_k"], "min_p": SAMPLING["min_p"],
                            "repeat_penalty": SAMPLING["repeat_penalty"],
                            "chat_template_kwargs": {"enable_thinking": False}})
        else:
            payload["model"] = settings.get("llm.model") or ""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    key = settings.get("llm.api_key")
    if key and provider == "openai":
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=float(settings.get("llm.timeout_s") or 15)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc
    try:
        if provider == "ollama":
            text = body["message"]["content"]
        else:
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


def run_command(settings, command: str, selection: str, app: str, title: str,
                base_url: str | None = None, provider: str = "openai") -> str:
    parts = [f"Команда: {command}"]
    if selection.strip():
        parts.append(f"Выделенный текст:\n<<<\n{selection}\n>>>")
    if settings.get("llm.use_context", True) and (app or title):
        parts.append(f"Приложение: «{app}», окно «{title}».")
    instructions = (settings.get("llm.instructions") or "").strip()
    if instructions:
        parts.append(f"Общие предпочтения пользователя:\n{instructions}")
    return chat(settings, COMMAND_SYSTEM, "\n\n".join(parts), base_url, provider)


def list_models(settings) -> list[str]:
    base = (settings.get("llm.base_url") or "").rstrip("/")
    req = urllib.request.Request(f"{base}/models")
    key = settings.get("llm.api_key")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=4) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
