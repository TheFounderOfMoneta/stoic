"""Скачивание и извлечение текста статьи — локально, без Claude (не тратит лимиты).

trafilatura — лучший открытый извлекатель статей по независимым замерам. Текст раскладывается
на блоки (заголовки, абзацы, пункты списков, цитаты, код, таблицы, картинки) — Читалка рисует
свой документ из блоков, а перевод идёт блоками с номерами.

Пейволы не обходим: если текста почти нет или видны признаки подписки/капчи — статья
помечается, в Читалке будет «Коротко» и кнопка «Открыть на сайте».
"""
from __future__ import annotations

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from .util import detect_lang, domain_of, parse_date, words_count

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 Svodka/0.1")
MAX_BYTES = 5_000_000
MIN_WORDS = 120
CAPTCHA_MARKERS = ("captcha", "verify you are human", "are you a robot", "not a robot", "unusual activity",
                   "cf-browser-verification", "enable javascript and cookies", "access denied",
                   "checking your browser")
PAYWALL_MARKERS = ("subscribe to continue", "subscribe now", "to continue reading", "already a subscriber",
                   "sign in to read", "this article is for subscribers", "become a member to read",
                   "подпишитесь, чтобы", "доступно по подписке", "оформите подписку")

_domain_lock = threading.Lock()
_domain_last: dict[str, float] = {}


@dataclass
class Extracted:
    url: str
    status: str = "failed"          # ok | paywall | captcha | failed | short
    title: str = ""
    published: float | None = None
    lang: str = ""
    words: int = 0
    blocks: list = field(default_factory=list)   # [{"type": "p|h2|h3|li|quote|code|table|img", "text", "src"}]
    note: str = ""

    def lead(self, max_words: int = 800) -> str:
        """Начало статьи для Claude (чтобы оценить и написать «Коротко»)."""
        out, n = [], 0
        for b in self.blocks:
            if b["type"] in ("img", "code"):
                continue
            w = words_count(b["text"])
            if n + w > max_words and out:
                break
            out.append(b["text"])
            n += w
        return "\n\n".join(out)


def _polite(domain: str, gap: float = 1.0) -> None:
    """Не чаще одного запроса в секунду к одному сайту."""
    with _domain_lock:
        wait = _domain_last.get(domain, 0.0) + gap - time.time()
        _domain_last[domain] = max(time.time(), _domain_last.get(domain, 0.0) + gap)
    if wait > 0:
        time.sleep(wait)


def fetch(url: str, timeout: float = 20.0) -> tuple[str, str, int]:
    """(итоговый адрес, html, код ответа). Ограничение размера и таймауты."""
    _polite(domain_of(url))
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
               "Accept-Language": "en,ru;q=0.8"}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        with client.stream("GET", url) as resp:
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            try:
                html = raw.decode(encoding, errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            return str(resp.url), html, resp.status_code


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def parse_blocks(xml_text: str, base_url: str = "") -> tuple[list, dict]:
    """XML trafilatura → блоки + метаданные."""
    root = ET.fromstring(xml_text)
    meta = dict(root.attrib)
    main = root.find("main")
    blocks: list[dict] = []
    if main is None:
        return blocks, meta
    for el in main:
        tag = el.tag
        if tag == "head":
            rend = el.get("rend", "h2")
            text = _text(el)
            if text:
                blocks.append({"type": "h3" if rend in ("h3", "h4", "h5", "h6") else "h2", "text": text})
        elif tag == "p":
            text = _text(el)
            if text:
                blocks.append({"type": "p", "text": text})
        elif tag == "list":
            for item in el.findall("item"):
                text = _text(item)
                if text:
                    blocks.append({"type": "li", "text": text})
        elif tag == "quote":
            text = _text(el)
            if text:
                blocks.append({"type": "quote", "text": text})
        elif tag == "code":
            text = "".join(el.itertext()).strip("\n")
            if text:
                blocks.append({"type": "code", "text": text})
        elif tag == "table":
            rows = []
            for row in el.findall("row"):
                cells = [_text(c) for c in row]
                rows.append(" | ".join(c for c in cells if c))
            text = "\n".join(r for r in rows if r)
            if text:
                blocks.append({"type": "table", "text": text})
        elif tag == "graphic":
            src = el.get("src") or ""
            if src and not src.startswith("data:"):
                blocks.append({"type": "img", "text": el.get("alt") or el.get("title") or "",
                               "src": urljoin(base_url, src)})
    # заголовок статьи дублируется первым h1 — убираем
    title = (meta.get("title") or "").strip()
    if blocks and blocks[0]["type"] in ("h2", "h3") and blocks[0]["text"].strip() == title:
        blocks.pop(0)
    return clean_blocks(blocks), meta


_DATE_LINE = re.compile(r"^(updated|published|posted|обновлено|опубликовано|дата)?[\s:,]*"
                        r"\d[\d.\-/:, ]*(am|pm|gmt|utc|msk)?[\s+\-\d:]*$", re.I)


def clean_blocks(blocks: list[dict]) -> list[dict]:
    """Служебный мусор сайта: строки-даты и короткие ярлыки рубрик перед началом текста."""
    out = [b for b in blocks if not (b["type"] == "p" and _DATE_LINE.match(b["text"].strip()))]
    first_real = next((i for i, b in enumerate(out) if b["type"] == "p" and words_count(b["text"]) >= 12), 0)
    head = [b for b in out[:first_real] if not (b["type"] == "p" and words_count(b["text"]) <= 3)]
    return head + out[first_real:]


def extract_html(html: str, url: str) -> Extracted:
    import trafilatura
    out = Extracted(url=url)
    low = html[:200_000].lower()
    try:
        xml_text = trafilatura.extract(html, url=url, output_format="xml", include_images=True,
                                       include_tables=True, include_comments=False, include_links=False,
                                       with_metadata=True, favor_recall=True)
    except Exception as exc:  # noqa: BLE001 — извлекатель не должен ронять сбор
        out.note = f"не удалось разобрать: {exc}"
        return out
    if not xml_text:
        out.status = "captcha" if any(m in low for m in CAPTCHA_MARKERS) else "failed"
        return out
    try:
        blocks, meta = parse_blocks(xml_text, url)
    except ET.ParseError as exc:
        out.note = f"разбор XML: {exc}"
        return out
    out.blocks = blocks
    out.title = (meta.get("title") or "").strip()
    out.published = parse_date(meta.get("date"))
    text = " ".join(b["text"] for b in blocks if b["type"] != "img")
    out.words = words_count(text)
    out.lang = detect_lang(text) or detect_lang(out.title)
    if out.words < MIN_WORDS:
        if any(m in low for m in CAPTCHA_MARKERS):
            out.status = "captcha"
        elif any(m in low for m in PAYWALL_MARKERS):
            out.status = "paywall"
        else:
            out.status = "short"
        return out
    out.status = "paywall" if out.words < 350 and any(m in low for m in PAYWALL_MARKERS) else "ok"
    return out


def read_article(url: str, timeout: float = 20.0) -> Extracted:
    """Скачать и извлечь. Никогда не бросает — статус в результате."""
    try:
        final_url, html, code = fetch(url, timeout=timeout)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return Extracted(url=url, status="failed", note=f"не скачалось: {type(exc).__name__}")
    if code >= 400:
        status = "paywall" if code in (401, 402, 403) else "failed"
        return Extracted(url=final_url, status=status, note=f"ответ сайта {code}")
    return extract_html(html, final_url)


def host(url: str) -> str:
    return urlsplit(url).netloc
