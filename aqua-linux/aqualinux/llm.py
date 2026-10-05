"""Языковые модели: DeepSeek API (основной), встроенная Qwen3.5-0.8B (llama-server), Ollama, свой сервер.

Порядок: если вставлен ключ DeepSeek — сначала DeepSeek; не ответил (нет сети, ключ не подошёл,
таймаут) — запасная локальная модель. После сбоя DeepSeek какое-то время не дёргаем, чтобы
каждая диктовка не ждала таймаут.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com"
# Первое — по умолчанию; остальные пробуются, если API скажет, что модели нет.
DEEPSEEK_MODELS = ["deepseek-flash", "deepseek-v4-flash", "deepseek-chat"]

COMMAND_SYSTEM = (
    "Ты — редактор текста. Пользователь голосом даёт команду, что сделать с текстом "
    "(сократить, исправить, перевести, сделать вежливее и т. п.). Текст передаётся между тегами "
    "<текст> и </текст>, команда — после слова «Команда». Примени команду к тексту и верни ТОЛЬКО "
    "новый вариант текста: без тегов, без пояснений, без кавычек и markdown. Не добавляй ничего от себя, "
    "не повторяй команду и эти правила. Если текста нет — выполни команду и верни только готовый текст "
    "для вставки."
)
STYLE = ("Отвечай на языке текста (по умолчанию по-русски). Пиши грамотно и естественно. "
         "Строго соблюдай заданный формат, число пунктов и ограничения длины.")

# Рекомендации Qwen3.5 для текстовых задач без «размышлений» (профиль пользователя).
SAMPLING = {"temperature": 1.0, "top_p": 1.0, "top_k": 20, "min_p": 0.0, "presence_penalty": 2.0,
            "repeat_penalty": 1.0}


class LLMError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, fatal: bool = False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal      # ключ не подошёл / нет денег — повтор не поможет


@dataclass
class Provider:
    kind: str          # deepseek | builtin | ollama | openai
    base: str
    model: str = ""
    key: str = ""
    label: str = ""

    @property
    def cloud(self) -> bool:
        return self.kind == "deepseek"


# ---------------------------------------------------------------- запрос
_ds_lock = threading.Lock()
_ds_state = {"thinking_param": True, "model": None}


def _post(url: str, body: dict, key: str, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=max(0.3, timeout)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read()[:400].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        raise LLMError(f"HTTP {exc.code}: {detail}", status=exc.code,
                       fatal=exc.code in (401, 402, 403)) from exc
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 — нет сети, таймаут, DNS
        raise LLMError(str(exc) or exc.__class__.__name__) from exc


def request(provider: Provider, messages: list[dict], *, max_tokens: int, timeout_s: float,
            greedy: bool = True, time_limit_ms: Optional[int] = None, keep_alive=-1) -> str:
    """Один запрос к модели. greedy — для исправления (почти дословное повторение текста)."""
    kind = provider.kind
    if kind == "ollama":
        options = {"temperature": 0, "top_k": 1} if greedy else dict(SAMPLING)
        options.update({"num_predict": max_tokens, "num_ctx": 4096})
        body = {"model": provider.model or "qwen3.5:0.8b-local", "messages": messages, "stream": False,
                "think": False, "keep_alive": keep_alive, "options": options}
        data = _post(f"{provider.base}/api/chat", body, "", timeout_s)
        if data.get("done_reason") == "length":
            raise LLMError("ответ не уложился в лимит")
        return _strip(((data.get("message") or {}).get("content")) or "")

    body: dict = {"messages": messages, "max_tokens": max_tokens, "stream": False}
    if kind == "deepseek":
        body["temperature"] = 0.0 if greedy else 0.7
    elif greedy:
        body["temperature"] = 0
    else:
        body.update({"temperature": SAMPLING["temperature"], "top_p": SAMPLING["top_p"],
                     "presence_penalty": SAMPLING["presence_penalty"]})
    if kind == "builtin":
        body.update({"model": "qwen", "chat_template_kwargs": {"enable_thinking": False}, "cache_prompt": True})
        if greedy:
            body["top_k"] = 1
            if time_limit_ms:
                body["t_max_predict_ms"] = int(time_limit_ms)
        else:
            body.update({"top_k": SAMPLING["top_k"], "min_p": SAMPLING["min_p"],
                         "repeat_penalty": SAMPLING["repeat_penalty"]})
        data = _post(f"{provider.base}/chat/completions", body, "", timeout_s)
    elif kind == "deepseek":
        data = _deepseek(provider, body, timeout_s)
    else:
        body["model"] = provider.model or ""
        data = _post(f"{provider.base}/chat/completions", body, provider.key, timeout_s)
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неожиданный ответ: {str(data)[:200]}") from exc
    if choice.get("finish_reason") == "length":
        raise LLMError("ответ не уложился в лимит")
    return _strip(content)


def _deepseek(provider: Provider, body: dict, timeout_s: float) -> dict:
    """DeepSeek: режим «без размышлений» (быстро и дёшево); подбор имени модели, если старое убрали."""
    deadline = time.monotonic() + timeout_s
    with _ds_lock:
        models = [_ds_state["model"] or provider.model or DEEPSEEK_MODELS[0]]
        thinking_param = _ds_state["thinking_param"]
    for name in [provider.model] + DEEPSEEK_MODELS:
        if name and name not in models:
            models.append(name)
    last: Optional[LLMError] = None
    for model in models:
        payload = dict(body, model=model)
        if thinking_param:
            payload["thinking"] = {"type": "disabled"}
        try:
            data = _post(f"{provider.base}/chat/completions", payload, provider.key,
                         deadline - time.monotonic())
        except LLMError as exc:
            last = exc
            text = str(exc).lower()
            if exc.status == 400 and thinking_param and "thinking" in text:
                # Старый API не знает параметр — повторяем без него.
                thinking_param = False
                with _ds_lock:
                    _ds_state["thinking_param"] = False
                payload.pop("thinking", None)
                try:
                    data = _post(f"{provider.base}/chat/completions", payload, provider.key,
                                 deadline - time.monotonic())
                except LLMError as exc2:
                    last = exc2
                    continue
            elif exc.status in (400, 404) and "model" in text and time.monotonic() < deadline - 0.5:
                continue
            else:
                raise
        with _ds_lock:
            _ds_state["model"] = model
        return data
    raise last or LLMError("DeepSeek не ответил")


_FENCE = re.compile(r"^```[\w-]*\n?|\n?```$")


def _strip(text: str) -> str:
    text = (text or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    return text


def list_deepseek_models(key: str, base: str = DEEPSEEK_URL, timeout: float = 8.0) -> list[str]:
    req = urllib.request.Request(f"{base.rstrip('/')}/models")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code}", status=exc.code, fatal=exc.code in (401, 402, 403)) from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMError(str(exc)) from exc
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def explain(exc: Exception) -> str:
    """Понятное человеку объяснение ошибки облачного ИИ."""
    status = getattr(exc, "status", None)
    text = str(exc).lower()
    if status == 401 or "authentication" in text or "api key" in text:
        return "ключ DeepSeek не подошёл"
    if status == 402 or "insufficient" in text or "balance" in text:
        return "на счёте DeepSeek закончились деньги"
    if status == 429:
        return "DeepSeek просит подождать (слишком много запросов)"
    if status and status >= 500:
        return "DeepSeek временно недоступен"
    if "timed out" in text or "timeout" in text:
        return "DeepSeek не ответил вовремя"
    if "name or service" in text or "nodename" in text or "unreachable" in text or "refused" in text:
        return "нет связи с DeepSeek"
    return "DeepSeek не ответил"


# ---------------------------------------------------------------- маршрутизатор
class Router:
    """Выбирает модель: DeepSeek → запасная локальная. Потокобезопасный."""

    COOLDOWN_S = 45.0
    FATAL_COOLDOWN_S = 600.0

    def __init__(self, settings, local_llm=None):
        self.settings = settings
        self.local_llm = local_llm
        self._lock = threading.Lock()
        self._cloud_off_until = 0.0
        self.last_cloud_error = ""
        self.last_used = ""
        self.on_cloud_failed: Optional[Callable[[str, bool], None]] = None   # (объяснение, фатально)
        self.on_need_local: Optional[Callable[[], None]] = None             # запустить запасную модель

    # ------------------------------------------------------------ состав
    def cloud(self) -> Optional[Provider]:
        s = self.settings
        key = (s.get("llm.deepseek_key") or "").strip()
        if not key or not s.get("llm.cloud", True):
            return None
        base = (s.get("llm.deepseek_url") or DEEPSEEK_URL).rstrip("/")
        return Provider("deepseek", base, (s.get("llm.deepseek_model") or DEEPSEEK_MODELS[0]).strip(), key,
                        "DeepSeek")

    def local_kind(self) -> str:
        value = self.settings.get("llm.provider", "builtin") or "builtin"
        return "openai" if value == "external" else value

    def local(self) -> Optional[Provider]:
        s = self.settings
        kind = self.local_kind()
        if kind == "builtin":
            llm = self.local_llm
            if llm is None or not getattr(llm, "ready", False):
                return None
            return Provider("builtin", llm.base_url, "qwen", label="Qwen3.5-0.8B (локально)")
        if kind == "ollama":
            base = (s.get("llm.ollama_url") or "http://127.0.0.1:11434").rstrip("/")
            return Provider("ollama", base, s.get("llm.ollama_model") or "qwen3.5:0.8b-local",
                            label=f"Ollama · {s.get('llm.ollama_model')}")
        base = (s.get("llm.base_url") or "").rstrip("/")
        if not base:
            return None
        return Provider("openai", base, s.get("llm.model") or "", s.get("llm.api_key") or "", "свой сервер")

    def local_configured(self) -> bool:
        kind = self.local_kind()
        if kind == "builtin":
            return self.local_llm is not None and self.local_llm.installed()
        if kind == "ollama":
            return True
        return bool((self.settings.get("llm.base_url") or "").strip())

    def available(self) -> bool:
        return self.cloud() is not None or self.local() is not None

    def cloud_cooling(self) -> bool:
        with self._lock:
            return time.monotonic() < self._cloud_off_until

    def chain(self) -> list[Provider]:
        out = []
        cloud = self.cloud()
        if cloud is not None and not self.cloud_cooling():
            out.append(cloud)
        local = self.local()
        if local is not None:
            out.append(local)
        return out

    # ------------------------------------------------------------ вызов
    def call(self, messages: list[dict], *, max_tokens: int, deadline: float, greedy: bool = True,
             local_time_limit_ms: Optional[int] = None) -> tuple[str, Provider]:
        """Ответ первой сработавшей модели. deadline — time.monotonic(), к которому нужен ответ."""
        errors = []
        chain = self.chain()
        if not chain and self.cloud() is not None:
            self._want_local()
        for provider in chain:
            remaining = deadline - time.monotonic()
            if remaining < 0.25:
                break
            try:
                keep_alive = -1 if self.cloud() is None else "10m"
                text = request(provider, messages, max_tokens=max_tokens, timeout_s=remaining, greedy=greedy,
                               time_limit_ms=local_time_limit_ms if provider.kind == "builtin" else None,
                               keep_alive=keep_alive)
                self.last_used = provider.label or provider.kind
                if provider.cloud:
                    with self._lock:
                        self._cloud_off_until = 0.0
                    self.last_cloud_error = ""
                return text, provider
            except LLMError as exc:
                errors.append(f"{provider.kind}: {exc}")
                log.warning("ИИ %s не ответил: %s", provider.kind, exc)
                if provider.cloud:
                    self._cloud_failed(exc)
        raise LLMError("; ".join(errors) or "ИИ недоступен")

    def _cloud_failed(self, exc: LLMError) -> None:
        reason = explain(exc)
        fatal = bool(getattr(exc, "fatal", False))
        with self._lock:
            self._cloud_off_until = time.monotonic() + (self.FATAL_COOLDOWN_S if fatal else self.COOLDOWN_S)
        first = reason != self.last_cloud_error
        self.last_cloud_error = reason
        self._want_local()
        if self.on_cloud_failed and first:
            try:
                self.on_cloud_failed(reason, fatal)
            except Exception:  # noqa: BLE001
                pass

    def _want_local(self) -> None:
        if self.local() is None and self.local_configured() and self.on_need_local:
            try:
                self.on_need_local()
            except Exception:  # noqa: BLE001
                pass

    def reset_cloud(self) -> None:
        with self._lock:
            self._cloud_off_until = 0.0
        self.last_cloud_error = ""


# ---------------------------------------------------------------- Edit Mode
_TAG = re.compile(r"</?\s*текст\s*>", re.IGNORECASE)
_ECHO = re.compile(r"^\s*(команда|текст|ответ|результат|новый вариант|исправленный текст)\s*:\s*",
                   re.IGNORECASE)


def command_messages(settings, command: str, selection: str, app: str = "") -> list[dict]:
    system = f"{COMMAND_SYSTEM}\n{STYLE}"
    instructions = (settings.get("llm.instructions") or "").strip()
    if instructions:
        system += f"\nПредпочтения пользователя (учитывай молча, в ответ не включай): {instructions}"
    if settings.get("llm.use_context", False) and app:
        system += f"\nТекст будет вставлен в приложение «{app}»."
    if selection.strip():
        user = f"<текст>\n{selection.strip()}\n</текст>\n\nКоманда: {command.strip()}"
    else:
        user = f"Команда: {command.strip()}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def clean_command_output(text: str, command: str, settings) -> str:
    """Убрать то, что маленькие модели любят повторять: теги, «Команда: …», правила пользователя."""
    text = _TAG.sub("", text or "").strip()
    lines = [ln for ln in text.splitlines()]
    while lines and _ECHO.match(lines[0]) and command.strip().lower() in lines[0].lower():
        lines.pop(0)
    text = "\n".join(lines).strip()
    text = _ECHO.sub("", text, count=1) if _ECHO.match(text) else text
    instructions = (settings.get("llm.instructions") or "").strip()
    if instructions:
        for piece in re.split(r"(?<=[.!?\n])\s+", instructions):
            piece = piece.strip()
            if len(piece) > 12 and piece in text:
                text = text.replace(piece, "")
        text = re.sub(r"\s*(Общие|Пользовательские)\s+(настройки|предпочтения)[^:]*:\s*$", "", text,
                      flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if len(text) > 2 and text[0] in "«\"" and text[-1] in "»\"" and text.count(text[0]) == 1:
        text = text[1:-1].strip()
    return text


def run_command(settings, router: Router, command: str, selection: str, app: str = "") -> str:
    """Edit Mode: применить голосовую команду к выделенному тексту."""
    timeout = float(settings.get("llm.timeout_s") or 15)
    max_tokens = min(4096, int(len(selection) / 1.5) + 256)
    text, provider = router.call(command_messages(settings, command, selection, app), max_tokens=max_tokens,
                                 deadline=time.monotonic() + timeout, greedy=False)
    out = clean_command_output(text, command, settings)
    if not out:
        raise LLMError("пустой ответ")
    return out


def chat(settings, router: Router, system: str, user: str, timeout_s: float = 15.0) -> str:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, _ = router.call(messages, max_tokens=256, deadline=time.monotonic() + timeout_s, greedy=True)
    return text


def list_models(settings) -> list[str]:
    base = (settings.get("llm.base_url") or "").rstrip("/")
    req = urllib.request.Request(f"{base}/models")
    key = settings.get("llm.api_key")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=4) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
