"""Свежие заголовки без Claude: RSS-поиск Bing News и ленты изданий. Бесплатно и быстро.

Раньше кандидатов искал сам Claude (десятки поисков и чтений в одном длинном диалоге) — это
съедало больше всего лимита подписки. Теперь заголовки собираются здесь, локально:
- Bing News RSS по запросам каждой темы — только за последние сутки, сразу прямые ссылки;
- ленты хороших изданий (ИИ и технологии, США, Китай, Россия, мир);
- ваши сохранённые запросы и идеи для разведки из еженедельного разбора.
Затем: отсев старого, уже виденного и скрытого вами, склейка одинаковых сюжетов, предварительная
оценка (тема × вес, сколько изданий пишут, ваше отношение к источнику, свежесть). Claude получает
уже короткий список и только выбирает.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from . import search
from .extract import UA, _polite
from .util import detect_lang, domain_of, norm_entity, normalize_url, parse_date

log = logging.getLogger(__name__)

BING = "https://www.bing.com/news/search?q={q}&format=rss&mkt={mkt}{fresh}"
BING_FRESH = "&qft=interval%3d%227%22"          # за последние 24 часа
POOL_SIZE = 90             # столько строк видит Claude при отборе (≈ 6–8 тыс. токенов)
PER_ORIGIN = 12
PER_DOMAIN = 8
PER_TOPIC_MIN = 8         # гарантированные места для каждой вашей темы
AREA_SHARE = 0.2          # не больше 20% списка из лент одного направления

# Ленты изданий: area — подсказка, к чему относится лента (тему решает Claude).
DEFAULT_FEEDS = [
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "area": "ai"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "area": "ai"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index", "area": "tech"},
    {"url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "area": "ai"},
    {"url": "https://openai.com/news/rss.xml", "area": "ai"},
    {"url": "https://spectrum.ieee.org/feeds/feed.rss", "area": "tech"},
    {"url": "https://rss.politico.com/politics-news.xml", "area": "us"},
    {"url": "https://www.lawfaremedia.org/feeds/articles", "area": "us"},
    {"url": "https://www.semafor.com/rss.xml", "area": "world"},
    {"url": "https://www.scmp.com/rss/4/feed", "area": "china"},
    {"url": "https://thediplomat.com/feed/", "area": "china"},
    {"url": "https://www.vedomosti.ru/rss/news", "area": "russia"},
    {"url": "https://www.kommersant.ru/RSS/news.xml", "area": "russia"},
    {"url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "area": "russia"},
    {"url": "https://www.interfax.ru/rss.asp", "area": "russia"},
    {"url": "https://meduza.io/rss/all", "area": "russia"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "area": "world"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "area": "world"},
]

# Поисковые запросы для стартовых тем (для новых тем их один раз придумывает Claude).
SEED_QUERIES = {
    "ИИ: развитие": {"en": ["OpenAI Anthropic Google AI model", "AI research breakthrough", "AI agents",
                            "AI chips data centers"],
                     "ru": ["искусственный интеллект"]},
    "Новые технологии": {"en": ["technology breakthrough", "robotics", "quantum computing",
                                "battery energy breakthrough"],
                         "ru": ["новая технология разработка"]},
    "Китай: внутренняя политика": {"en": ["China State Council", "Communist Party plenum", "Beijing policy reform",
                                          "China economy policy"],
                                   "ru": ["Китай реформа Пекин"]},
    "США: внутренняя политика": {"en": ["US Congress bill", "Trump executive order", "US Supreme Court ruling",
                                        "federal agency regulation Washington"],
                                 "ru": []},
    "Россия: внутренняя политика": {"en": [],
                                    "ru": ["Госдума законопроект", "правительство РФ постановление", "Минцифры",
                                           "Кремль указ"]},
    "Внешняя политика Китая, США и России": {"en": ["US China relations", "Russia foreign policy"],
                                             "ru": ["внешняя политика России"]},
}


# Надёжность источника: проверенные издания — выше, агрегаторы-перепечатки — ниже, неизвестные из
# поиска — чуть ниже нейтрального. Ленты из списка выше считаются проверенными. Дальше лента всё равно
# учится на вас (ваше отношение к источнику — отдельно).
TRUSTED = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com", "washingtonpost.com",
    "economist.com", "theatlantic.com", "politico.com", "axios.com", "semafor.com", "npr.org", "pbs.org",
    "lawfaremedia.org", "justsecurity.org", "brookings.edu", "csis.org", "carnegieendowment.org", "rand.org",
    "theverge.com", "techcrunch.com", "arstechnica.com", "wired.com", "technologyreview.com", "spectrum.ieee.org",
    "nature.com", "science.org", "newscientist.com", "quantamagazine.org", "theinformation.com", "404media.co",
    "openai.com", "anthropic.com", "deepmind.google", "blog.google", "huggingface.co", "arxiv.org",
    "scmp.com", "caixinglobal.com", "sixthtone.com", "thediplomat.com", "chinamediaproject.org", "nikkei.com",
    "asia.nikkei.com", "bbc.com", "bbc.co.uk", "aljazeera.com", "theguardian.com", "dw.com", "france24.com",
    "vedomosti.ru", "kommersant.ru", "rbc.ru", "interfax.ru", "meduza.io", "forbes.ru", "cnews.ru", "habr.com",
    "thebell.io", "novayagazeta.eu", "re-russia.net", "carnegie.ru", "tass.ru", "ria.ru", "frankmedia.ru",
}
LOW = {"msn.com", "aol.com", "rambler.ru", "dzen.ru", "yandex.ru", "newsnow.co.uk", "newspoint.app",
       "ground.news", "inkl.com", "flipboard.com", "headtopics.com", "newsbreak.com", "ua.news",
       # пресс-релизы — реклама, а не новости
       "prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com", "accesswire.com",
       "newswire.com", "prweb.com"}
# Платные издания: в приложении их не прочитать — только если событие очень важное
PAYWALLED = {"ft.com", "wsj.com", "bloomberg.com", "economist.com", "theinformation.com", "nikkei.com",
             "asia.nikkei.com", "theathletic.com", "barrons.com", "caixinglobal.com", "thetimes.co.uk"}


def _matches(domain: str, names: set) -> bool:
    parts = domain.lower().split(".")
    return any(".".join(parts[i:]) in names for i in range(len(parts) - 1))


def is_paywalled(domain: str) -> bool:
    return _matches(domain, PAYWALLED)


def source_quality(domain: str, from_feed: bool = False) -> float:
    d = domain.lower()
    parts = d.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in TRUSTED:
            return 0.45
        if cand in LOW:
            return -0.6
    return 0.3 if from_feed else -0.15


@dataclass
class Candidate:
    url: str
    title: str
    source: str = ""
    published: float | None = None
    snippet: str = ""
    origin: str = ""            # «q:запрос» или «feed:адрес»
    topic: str = ""             # тема запроса, если найдено по запросу темы
    area: str = ""
    score: float = 0.0
    also: list = field(default_factory=list)      # другие издания о том же сюжете

    @property
    def key(self) -> str:
        return normalize_url(self.url)

    @property
    def domain(self) -> str:
        return domain_of(self.url)


# ---------------------------------------------------------------- загрузка и разбор
def _get(url: str, timeout: float = 15.0) -> str:
    _polite(domain_of(url))
    r = httpx.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                  timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _clean(text: str, limit: int = 220) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def _date(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text.strip()).timestamp()
    except (TypeError, ValueError, IndexError):
        return parse_date(text.strip())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, origin: str = "", area: str = "", topic: str = "") -> list[Candidate]:
    """RSS 2.0 и Atom → кандидаты. Ломаные ленты — пусто, а не ошибка."""
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8", "replace"))
    except ET.ParseError:
        return []
    channel = ""
    for el in root.iter():
        if _local(el.tag) == "title" and el.text:
            channel = el.text.strip()
            break
    out = []
    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue
        f: dict = {}
        for ch in item:
            name = _local(ch.tag)
            if name == "link" and ch.get("href"):
                if ch.get("rel", "alternate") == "alternate":
                    f["link"] = ch.get("href")
            elif name in ("title", "link", "description", "summary", "pubdate", "published", "updated", "date",
                          "source", "encoded"):
                f.setdefault(name, (ch.text or "").strip())
        link = f.get("link", "")
        if "bing.com/news/apiclick" in link:                 # Bing отдаёт ссылку-переходник
            link = parse_qs(urlsplit(link).query).get("url", [link])[0]
        title = _clean(f.get("title", ""), 300)
        if not link.startswith("http") or not title:
            continue
        out.append(Candidate(
            url=link, title=title, source=f.get("source") or channel or domain_of(link),
            published=_date(f.get("pubdate") or f.get("published") or f.get("updated") or f.get("date")),
            snippet=_clean(f.get("description") or f.get("summary") or "", 110), origin=origin, area=area,
            topic=topic))
    return out


def bing_news(query: str, lang: str = "en", fresh: bool = True, topic: str = "") -> list[Candidate]:
    mkt = "ru-RU" if lang == "ru" else "en-US"
    url = BING.format(q=quote_plus(query), mkt=mkt, fresh=BING_FRESH if fresh else "")
    try:
        return parse_feed(_get(url), origin=f"q:{query}", topic=topic)
    except (httpx.HTTPError, OSError) as exc:
        log.info("Bing News «%s»: %s", query, exc)
        return []


def feed(entry: dict) -> list[Candidate]:
    try:
        return parse_feed(_get(entry["url"]), origin=f"feed:{entry['url']}", area=entry.get("area", ""))
    except (httpx.HTTPError, OSError) as exc:
        log.info("лента %s: %s", entry.get("url"), exc)
        return []


# ---------------------------------------------------------------- запросы тем
def _topic_hash(t: dict) -> str:
    return hashlib.sha1(f"{t['name']}|{t.get('description', '')}".encode()).hexdigest()[:12]


def topic_queries(storage, topics: list[dict]) -> tuple[dict, list[dict]]:
    """{тема: {"en": [...], "ru": [...]}} и список тем, для которых запросов ещё нет."""
    out, missing = {}, []
    for t in topics:
        cached = json.loads(storage.meta_get(f"queries:{t['name']}", "") or "{}")
        if cached.get("hash") == _topic_hash(t):
            out[t["name"]] = cached
        elif t["name"] in SEED_QUERIES:
            out[t["name"]] = SEED_QUERIES[t["name"]]
        else:
            missing.append(t)
            out[t["name"]] = {"en": [], "ru": [t["name"]]}      # пока нет лучших — само название
    return out, missing


def save_topic_queries(storage, topic: dict, en: list[str], ru: list[str]) -> None:
    storage.meta_set(f"queries:{topic['name']}", json.dumps(
        {"hash": _topic_hash(topic), "en": [q for q in en if q][:4], "ru": [q for q in ru if q][:3]},
        ensure_ascii=False))


# ---------------------------------------------------------------- сбор и отбор
def gather(storage, settings, progress=lambda _m: None) -> list[Candidate]:
    """Все свежие заголовки из запросов тем, сохранённых запросов и лент (параллельно)."""
    topics = storage.topics()
    queries, _missing = topic_queries(storage, topics)
    jobs = []
    for t in topics:
        q = queries.get(t["name"], {})
        for lang in ("en", "ru"):
            for text in q.get(lang, []):
                jobs.append(("bing", text, lang, t["name"]))
    for sq in storage.saved_queries():
        jobs.append(("bing", sq["text"], detect_lang(sq["text"]) or "en", ""))
    weekly = json.loads(storage.meta_get("weekly_suggestions", "") or "{}")
    for idea in (weekly.get("explore_ideas") or [])[:3]:
        if isinstance(idea, str) and idea.strip():
            jobs.append(("bing", idea.strip()[:80], detect_lang(idea) or "en", ""))
    feeds = settings.get("collect.feeds", None) or DEFAULT_FEEDS
    jobs += [("feed", f, "", "") for f in feeds if isinstance(f, dict) and f.get("url")]
    progress(f"Собираю свежие заголовки: запросов {sum(1 for j in jobs if j[0] == 'bing')}, "
             f"лент {sum(1 for j in jobs if j[0] == 'feed')}…")

    def run(job):
        kind, a, lang, topic = job
        return bing_news(a, lang, topic=topic) if kind == "bing" else feed(a)
    out: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for items in pool.map(run, jobs):
            out.extend(items)
    return out


def _title_key(title: str) -> frozenset:
    return frozenset(s for s in search.stems(title) if len(s) > 2)


def _same_story(a: frozenset, b: frozenset, common: set | None = None) -> bool:
    """Один сюжет: заголовки сильно пересекаются, или общие хотя бы два редких слова
    («Минцифры», «МФЦ») при заметном пересечении — так склеиваются разные формулировки."""
    if not a or not b:
        return False
    shared = a & b
    jac = len(shared) / len(a | b)
    if jac >= 0.5:
        return True
    rare = shared - (common or set())
    return jac >= 0.2 and len(rare) >= 2


def prepare(storage, settings, cands: list[Candidate], model=None, now: float | None = None,
            limit: int = POOL_SIZE) -> list[Candidate]:
    """Отсев, склейка сюжетов, предварительная оценка и ограничение размера списка для Claude."""
    now = now if now is not None else time.time()
    max_age = float(settings.get("collect.max_age_hours", 72)) * 3600
    rules = storage.active_rules()
    muted_domains = {r["target"].lower() for r in rules if r["kind"] == "mute_source"}
    muted_entities = [norm_entity(r["target"]) for r in rules if r["kind"] == "mute_entity"]
    weights = {t["name"]: float(t["weight"]) for t in storage.topics()}
    keywords = set()
    for t in storage.topics():
        keywords |= {s for s in search.stems(f"{t['name']} {t.get('description', '')}") if len(s) > 3}
    known = storage.known_url_keys([c.key for c in cands])
    offered = storage.offered_recently([c.key for c in cands])
    seen_keys: set[str] = set()
    fresh: list[Candidate] = []
    for c in cands:
        if c.key in known or c.key in offered or c.key in seen_keys:
            continue
        if c.published and now - c.published > max_age:
            continue
        if c.domain.lower() in muted_domains:
            continue
        low = c.title.lower()
        if any(e and e in low for e in muted_entities):
            continue
        seen_keys.add(c.key)
        fresh.append(c)
    # склейка одного сюжета из разных изданий: оставляем лучший, остальные — «тоже пишут»
    keys = [_title_key(c.title) for c in fresh]
    df: dict[str, int] = {}
    for k in keys:
        for w in k:
            df[w] = df.get(w, 0) + 1
    common = {w for w, n in df.items() if n > max(3, 0.03 * len(keys))}      # слишком частые слова
    groups: list[tuple[frozenset, Candidate]] = []
    for c, k in zip(fresh, keys):
        for gk, head in groups:
            if _same_story(k, gk, common):
                head.also.append(c.source or c.domain)
                if c.topic and not head.topic:
                    head.topic = c.topic
                break
        else:
            groups.append((k, c))
    rare_keywords = keywords - common - {w for w, n in df.items() if n > 0.01 * len(keys) and n > 2}
    for k, c in groups:
        from_feed = c.origin.startswith("feed:")
        s = 1.0 * weights.get(c.topic, 1.0) if c.topic else 0.2       # найдено по запросу темы — главное
        if rare_keywords & set(k):
            s += 0.4                                                  # совпадает с вашей темой по сути
        s += min(0.6, 0.15 * len(c.also))                             # о сюжете пишут несколько изданий
        s += source_quality(c.domain, from_feed=from_feed) * (0.8 if from_feed else 1.0)
        if model is not None:
            st = model.stats.get("source:" + c.domain)
            if st is not None:
                if st.beta < -0.5 and st.ev_long >= 3:
                    c.score = -1.0                                    # источник вам явно не нравится
                    continue
                s += 0.5 * max(-0.4, min(0.4, st.beta))
        if c.published:
            s += 0.3 * math.exp(-max(0.0, now - c.published) / 86400)
        c.score = s
    ranked = sorted((c for _k, c in groups if c.score >= 0), key=lambda c: c.score, reverse=True)
    per_origin: dict[str, int] = {}
    per_domain: dict[str, int] = {}
    per_area: dict[str, int] = {}
    area_cap = max(6, int(limit * AREA_SHARE))
    pool: list[Candidate] = []
    taken: set[int] = set()

    def take(c: Candidate) -> bool:
        area = c.topic or c.area or "?"
        if per_origin.get(c.origin, 0) >= PER_ORIGIN or per_domain.get(c.domain, 0) >= PER_DOMAIN:
            return False
        if not c.topic and per_area.get(area, 0) >= area_cap:
            return False                                              # одна общая лента не забивает список
        per_origin[c.origin] = per_origin.get(c.origin, 0) + 1
        per_domain[c.domain] = per_domain.get(c.domain, 0) + 1
        per_area[area] = per_area.get(area, 0) + 1
        pool.append(c)
        taken.add(id(c))
        return True
    # сначала — гарантированные места каждой вашей теме (лучшее из её запросов)
    for name in weights:
        n = 0
        for c in ranked:
            if n >= PER_TOPIC_MIN or len(pool) >= limit:
                break
            if c.topic == name and id(c) not in taken and take(c):
                n += 1
    # дальше — по оценке
    for c in ranked:
        if len(pool) >= limit:
            break
        if id(c) not in taken:
            take(c)
    pool.sort(key=lambda c: c.score, reverse=True)
    return pool
