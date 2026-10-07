"""Экономный сбор: заголовки собирает приложение, Claude только выбирает и пишет «Коротко».

Было: Claude сам искал в интернете — десятки поисков и чтений в одном длинном диалоге, и каждый
шаг заново отправлял весь накопленный контекст. Это и съедало лимит подписки.

Стало (обычно в 5–10 раз дешевле):
1. Заголовки — локально и бесплатно (sources.py): Bing News за сутки по запросам тем + ленты изданий.
   Отсев старого и виденного, склейка сюжетов, предварительная оценка → ~120 строк.
2. Claude, вызов 1 (без инструментов): из 120 коротких строк выбирает ~20 для чтения.
3. Статьи скачиваются и извлекаются локально.
4. Claude, вызов 2 (без инструментов): по началу каждой статьи — заголовок по-русски, «Коротко»,
   тема, оценки; лишнее отбрасывает.
5. Сохранение — через ту же проверку, что и у MCP-сервера (дата, свежесть, повторы).
Если заголовков почти нет (нет сети до Bing и лент) — запасной путь: прежний сбор Claude с поиском.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import claude_cli, extract, sources
from .config import PROMPTS_DIR, RUNTIME_DIR, ensure_dirs
from .rank.brief import build_brief
from .rank.features import KINDS
from .rank.model import InterestModel

log = logging.getLogger(__name__)

MIN_POOL = 8              # меньше заголовков — источники недоступны, нужен запасной путь
LEAD_WORDS = 220          # столько начала статьи видит Claude (хватает для «Коротко» и оценки)

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "read": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "topic": {"type": "string"},
            "bucket": {"type": "string", "enum": ["core", "explore", "world"]}},
            "required": ["id", "topic", "bucket"]}},
    },
    "required": ["read"],
}
DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"},
            "keep": {"type": "boolean"},
            "title_ru": {"type": "string"},
            "summary_ru": {"type": "array", "items": {"type": "string"}},
            "why_ru": {"type": "string"},
            "topic": {"type": "string"},
            "subtopic": {"type": "string"},
            "entities": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": list(KINDS)},
            "bucket": {"type": "string", "enum": ["core", "explore", "world"]},
            "importance": {"type": "number"},
            "fit": {"type": "number"},
            "story_key": {"type": "string"}},
            "required": ["id", "keep", "title_ru", "summary_ru", "topic", "bucket", "kind", "importance", "fit"]}},
        "note": {"type": "string"},
    },
    "required": ["articles"],
}
QUERIES_SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"},
        "en": {"type": "array", "items": {"type": "string"}},
        "ru": {"type": "array", "items": {"type": "string"}}}, "required": ["name", "en", "ru"]}}},
    "required": ["topics"],
}


def ask_json(settings, prompt_file: str, payload: dict, schema: dict, model: str | None = None,
             cancel=None) -> tuple[dict, float]:
    """Один вызов Claude без инструментов, ответ — строго по схеме. (результат, стоимость)."""
    res = claude_cli.run(
        "Выполни задачу по входным данным и верни ответ строго по схеме.",
        ["--system-prompt-file", str(PROMPTS_DIR / prompt_file), "--tools", "", "--strict-mcp-config",
         "--mcp-config", '{"mcpServers":{}}', "--model", model or settings.get("claude.model", "sonnet"),
         "--json-schema", json.dumps(schema), "--max-turns", "3", "--no-session-persistence"],
        command=settings.get("claude.command", "claude"), stdin=json.dumps(payload, ensure_ascii=False),
        cwd=str(RUNTIME_DIR), timeout=600, cancel=cancel)
    return res.structured or {}, res.cost_usd


def compact_brief(storage, settings, model) -> dict:
    """Сводка о читателе — коротко: в отбор она идёт целиком, лишние токены здесь ни к чему."""
    b = build_brief(storage, settings, model=model)
    keep = ("сейчас", "свежесть", "темы", "профиль", "сколько статей", "правила", "следит за сюжетами",
            "сохранённые запросы")
    out = {k: b[k] for k in keep if k in b}
    out["что нравится"] = [f["признак"] for f in b.get("что нравится", [])[:10]]
    out["что не нравится"] = [f["признак"] for f in b.get("что не нравится", [])[:8]]
    out["зашло недавно"] = [x["заголовок"][:90] for x in b.get("зашло недавно", [])[:6]]
    out["не зашло недавно"] = [x["заголовок"][:90] for x in b.get("не зашло недавно", [])[:6]]
    out["уже было (не повторять)"] = [t[:90] for t in b.get("уже было за 3 дня (не повторять)", [])[:40]]
    for t in out.get("темы", []):
        for k in ("обязательные слова", "исключить"):
            if not t.get(k):
                t.pop(k, None)
    if b.get("идеи для разведки (еженедельный разбор)"):
        out["идеи для разведки"] = b["идеи для разведки (еженедельный разбор)"]
    return out


def ensure_queries(settings, storage, cancel=None) -> float:
    """Поисковые запросы для новых тем — один короткий вызов, потом из кэша."""
    _q, missing = sources.topic_queries(storage, storage.topics())
    if not missing:
        return 0.0
    payload = {"темы": [{"название": t["name"], "описание": t.get("description", "")} for t in missing]}
    try:
        out, cost = ask_json(settings, "queries.md", payload, QUERIES_SCHEMA, model="haiku", cancel=cancel)
    except claude_cli.ClaudeError as exc:
        log.info("запросы для тем: %s", exc.human())
        return 0.0
    by_name = {t["name"]: t for t in missing}
    for item in out.get("topics") or []:
        t = by_name.get(item.get("name"))
        if t:
            sources.save_topic_queries(storage, t, item.get("en") or [], item.get("ru") or [])
    return cost


def _iso(ts: float | None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) if ts else ""


def run(settings, storage, run_id: int, progress: Callable[[str], None], cancel=None) -> dict | None:
    """Экономный сбор. None — заголовков слишком мало, нужен запасной путь (сбор с поиском Claude)."""
    ensure_dirs()
    cost = ensure_queries(settings, storage, cancel)
    model = InterestModel.build(storage)
    raw = sources.gather(storage, settings, progress)
    pool = sources.prepare(storage, settings, raw, model=model)
    progress(f"Свежих заголовков: {len(raw)}, после отсева: {len(pool)}")
    if len(pool) < MIN_POOL:
        log.warning("мало заголовков (%s из %s) — запасной путь", len(pool), len(raw))
        return None
    storage.mark_offered([c.key for c in pool])
    brief = compact_brief(storage, settings, model)
    n_total = int(settings.get("collect.articles_per_run", 15))
    lines = [{"id": i, "заголовок": c.title, "источник": c.source,
              "часов назад": round((time.time() - c.published) / 3600) if c.published else None,
              "тема-подсказка": c.topic or c.area or None, "тоже пишут": len(c.also) or None,
              "проверенный": True if sources.source_quality(c.domain, c.origin.startswith("feed:")) > 0 else None,
              "платный": True if sources.is_paywalled(c.domain) else None,
              # описание — только если заголовок сам по себе мало что говорит (экономия токенов)
              "кратко": (c.snippet if len(c.title) < 70 and c.snippet[:40] not in c.title else None)}
             for i, c in enumerate(pool)]
    lines = [{k: v for k, v in x.items() if v not in (None, "")} for x in lines]

    # ---- 1. отбор по заголовкам
    progress(f"Claude выбирает интересное из {len(pool)} заголовков…")
    picked, c1 = ask_json(settings, "select.md", {"читатель": brief, "сколько выбрать": round(n_total * 1.5),
                                                  "заголовки": lines}, SELECT_SCHEMA, cancel=cancel)
    cost += c1
    chosen = []
    for item in picked.get("read") or []:
        i = item.get("id")
        if isinstance(i, int) and 0 <= i < len(pool) and all(i != x[0] for x in chosen):
            chosen.append((i, item))
    chosen = chosen[: round(n_total * 1.6)]
    if not chosen:
        return {"saved": 0, "cost": cost, "note": "Claude ничего не выбрал"}

    # ---- 2. чтение статей — локально
    progress(f"Читаю выбранные статьи: {len(chosen)}…")
    storage.mark_seen_urls(run_id, [pool[i].url for i, _ in chosen])

    def read(pair):
        i, _item = pair
        cached = storage.get_extracted(pool[i].url)
        if cached:
            return i, cached
        ex = extract.read_article(pool[i].url)
        storage.cache_extracted(ex)
        if ex.url != pool[i].url:
            storage.mark_seen_urls(run_id, [ex.url])
        return i, storage.get_extracted(ex.url) or storage.get_extracted(pool[i].url) or {}
    with ThreadPoolExecutor(max_workers=6) as ex_pool:
        pages = dict(ex_pool.map(read, chosen))
    articles = []
    for i, item in chosen:
        c, page = pool[i], pages.get(i) or {}
        status = page.get("status") or "failed"
        if status in ("failed", "captcha"):
            continue
        lead = extract.Extracted(url=c.url, blocks=page.get("blocks") or []).lead(LEAD_WORDS) if status == "ok" \
            else c.snippet
        articles.append({"id": i, "заголовок": page.get("title") or c.title, "источник": c.source,
                         "опубликовано": _iso(c.published or page.get("published")), "язык": page.get("lang", ""),
                         "слов": page.get("words", 0), "статус": status,
                         "тема-кандидат": item.get("topic", ""), "корзина-кандидат": item.get("bucket", ""),
                         "тоже пишут": c.also[:3], "начало": lead})
    if not articles:
        return {"saved": 0, "cost": cost, "note": "выбранные статьи не открылись"}

    # ---- 3. «Коротко» и оценки
    progress(f"Claude пишет «Коротко» для {len(articles)} статей…")
    digest, c2 = ask_json(settings, "digest.md", {"читатель": brief, "сколько оставить": n_total,
                                                  "статьи": articles}, DIGEST_SCHEMA, cancel=cancel)
    cost += c2
    from .mcp_server import Service
    svc = Service(storage, settings, run_id, "collect")
    items = []
    for a in digest.get("articles") or []:
        i = a.get("id")
        if not a.get("keep") or not isinstance(i, int) or not 0 <= i < len(pool) or i not in pages:
            continue
        c, page = pool[i], pages[i]
        if page.get("status") != "ok" and float(a.get("importance") or 0) < 0.8:
            continue                       # текста нет (платный, пустой) — берём только очень важное
        items.append({
            "url": page.get("url") or c.url, "title_ru": a.get("title_ru", ""),
            "title_orig": page.get("title") or c.title, "source": c.source,
            "published": _iso(c.published or page.get("published")),          # в RSS — точное время
            "summary_ru": a.get("summary_ru") or [], "why_ru": a.get("why_ru", ""), "topic": a.get("topic", ""),
            "subtopic": a.get("subtopic", ""), "entities": a.get("entities") or [], "kind": a.get("kind", "news"),
            "bucket": a.get("bucket", "core"), "importance": a.get("importance", 0.5), "fit": a.get("fit", 0.5),
            "story_key": a.get("story_key", "")})
    res = json.loads(svc.save_articles(items[: int(n_total * 1.6)]))
    for r in res.get("отклонено") or []:
        log.info("не сохранено %s: %s", r.get("url"), r.get("причина"))
    note = (digest.get("note") or "")[:500]
    storage.execute("UPDATE runs SET notes=? WHERE id=?",
                    (f"заголовков {len(raw)} → {len(pool)} → прочитано {len(articles)} → сохранено "
                     f"{res.get('сохранено', 0)}. {note}"[:2000], run_id))
    return {"saved": int(res.get("сохранено", 0)), "cost": cost, "note": note}
