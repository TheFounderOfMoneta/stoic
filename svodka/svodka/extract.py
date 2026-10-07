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
                   "checking your browser", "verifying your browser", "just a moment...")
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


CLEAN_VERSION = "2"      # меняется вместе с правилами очистки — сохранённые статьи перечищаются сами

_DATE_LINE = re.compile(r"^(updated|published|posted|обновлено|опубликовано|дата)?[\s:,]*"
                        r"\d[\d.\-/:, ]*(am|pm|gmt|utc|msk)?[\s+\-\d:]*$", re.I)
# «Opinion 16:15, 07-Oct-2026», «Бизнес-аналитика 16:48, 01.10.2026», «Updated Oct 5, 2026 3:10 PM»
_HAS_TIME_OR_DATE = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{1,2}-[A-Za-z]{3}-\d{4}\b|"
                               r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4}\b|"
                               r"\b\d{1,2} (января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|"
                               r"ноября|декабря)( \d{4})?\b", re.I)
_FOOTER = re.compile(r"copyright|©|all rights reserved|все права защищены|права защищены", re.I)
_COOKIE = re.compile(r"cookie|куки", re.I)
_CONSENT = re.compile(r"agree|consent|accept|privacy|policy|terms|соглас|конфиденциал|политик|услови", re.I)
# Призывы и служебные строки в конце страницы — только узнаваемые фразы, чтобы не задеть текст статьи
_PROMO = re.compile(r"contact us at|^contact:|follow us\b|follow @|subscribe to our|sign up for our|our newsletter|"
                    r"download (our|the) app|report hotline|hotline:|report an? (error|typo)|горячая линия:|"
                    r"сообщить об ошибке|подпишитесь на|подписывайтесь на|присоединяйтесь к нам|"
                    r"наш (телеграм|telegram)|telegram-канал|читайте (также|нас) в|скачайте приложение", re.I)
_CAPTION = re.compile(r"(/\s?[A-Z][A-Za-z]+(\s[A-Z][A-Za-z]+)?\.?$)|(^(photo|image|credit|фото|иллюстрация)\s*:)|"
                      r"(getty images|ap photo|reuters/|afp via|/xinhua|/vcg|/cfp|/ria|/тасс)", re.I)
_SENTENCE_END = re.compile(r"[.!?…:;»\")]$")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
# Подписи кнопок и меню целиком (сравниваются без регистра и знаков препинания)
_UI_LABELS = {
    "share", "copied", "share copied", "copy link", "link copied", "i agree", "agree", "accept", "accept all",
    "ok", "close", "follow", "follow us", "subscribe", "sign up", "sign in", "log in", "newsletter", "read more",
    "advertisement", "ad", "sponsored", "by", "icymi", "search trends", "download our app", "related", "related articles",
    "more from", "comments", "print", "email", "listen", "listen to this article", "save", "top stories",
    "поделиться", "скопировано", "ссылка скопирована", "согласен", "принять", "принимаю", "закрыть",
    "подписаться", "читать также", "читайте также", "реклама", "войти", "комментарии", "слушать", "сохранить",
}


def _norm_label(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def is_boilerplate(b: dict) -> bool:
    """Служебный блок сайта, а не текст статьи."""
    if b["type"] not in ("p", "li", "h2", "h3"):
        return False
    text = b["text"].strip()
    if not text or "{{" in text:
        return True
    n = words_count(text)
    label = _norm_label(text)
    if label in _UI_LABELS or (n <= 6 and re.match(r"^(follow|следите за|мы в)\b", label)):
        return True
    if b["type"] != "p":
        return False
    if _DATE_LINE.match(text):
        return True
    if n <= 8 and _HAS_TIME_OR_DATE.search(text) and not _SENTENCE_END.search(text):
        return True                                    # строка «рубрика + время/дата»
    if _COOKIE.search(text) and _CONSENT.search(text) and n < 80:
        return True                                    # баннер согласия на cookie
    words = text.split()
    if n >= 8 and not re.search(r"[.!?…;,]", text):
        caps = sum(1 for w in words if w[:1].isupper())
        if caps / len(words) >= 0.7:
            return True                                # меню разделов: «Главная Китай Мир Политика Бизнес …»
    if n < 30 and _CAPTION.search(text):
        return True                                    # подпись к фото: «… London, May 21. /Xinhua»
    return False


def _is_real_paragraph(b: dict) -> bool:
    return b["type"] == "p" and words_count(b["text"]) >= 12 and bool(re.search(r"[.!?…]", b["text"]))


def clean_blocks(blocks: list[dict]) -> list[dict]:
    """Убрать служебный мусор сайта, оставив текст статьи.

    Меню разделов, баннер cookie, кнопки («Поделиться», «Скопировано»), строки «рубрика + дата»,
    подписи к фото, шаблоны страницы ({{...}}); до первого настоящего абзаца — короткие строки
    (автор, рубрика); в конце — призывы подписаться и подвал после строки с копирайтом.
    Возвращает те же словари (не копии) — по ним перечищаются уже сохранённые статьи."""
    out = [b for b in blocks if not is_boilerplate(b)]
    # Строки на китайском/японском в некитайской статье — лицензии и подвал сайта (как у CGTN).
    allt = " ".join(b["text"] for b in out)
    if allt and len(_CJK.findall(allt)) / len(allt) < 0.2:
        out = [b for b in out if not (b["type"] == "p" and b["text"] and
                                      len(_CJK.findall(b["text"])) / len(b["text"]) > 0.3)]
    start = int(len(out) * 0.6)
    for i in range(start, len(out)):
        b = out[i]
        if b["type"] == "p" and words_count(b["text"]) < 25 and _FOOTER.search(b["text"]):
            out = out[:i]
            break
    tail_from = int(len(out) * 0.6)
    out = [b for i, b in enumerate(out)
           if not (i >= tail_from and b["type"] == "p" and words_count(b["text"]) < 45 and _PROMO.search(b["text"]))]
    first_real = next((i for i, b in enumerate(out) if _is_real_paragraph(b)), 0)
    head = [b for b in out[:first_real] if not (b["type"] == "p" and words_count(b["text"]) < 12)]
    return head + out[first_real:]


def reclean(storage) -> int:
    """Перечистить уже сохранённые статьи по новым правилам (один раз после обновления).

    Удаляются только блоки, которые новые правила считают мусором; перевод остальных сохраняется."""
    if storage.meta_get("clean_version", "") == CLEAN_VERSION:
        return 0
    changed = 0
    for row in storage.query("SELECT DISTINCT article_id FROM blocks"):
        aid = row["article_id"]
        stored = storage.blocks(aid)
        items = [dict(b, text=b["text_orig"]) for b in stored]
        keep = {id(b) for b in clean_blocks(items)}
        drop = [b["idx"] for b in items if id(b) not in keep]
        if drop:
            storage.execute(f"DELETE FROM blocks WHERE article_id=? AND idx IN ({','.join('?' * len(drop))})",
                            (aid, *drop))
            left = [b for b in items if id(b) in keep and b["type"] != "img"]
            storage.update_article(aid, words=sum(words_count(b["text"]) for b in left))
            from . import search
            search.reindex(storage, aid)
            changed += 1
    storage.execute("DELETE FROM extracted")            # кэш извлечения — по старым правилам
    storage.meta_set("clean_version", CLEAN_VERSION)
    return changed


def extract_html(html: str, url: str) -> Extracted:
    import trafilatura
    out = Extracted(url=url)
    low = html[:200_000].lower()
    def run(recall: bool):
        return trafilatura.extract(html, url=url, output_format="xml", include_images=True, include_tables=True,
                                   include_comments=False, include_links=False, with_metadata=True,
                                   favor_recall=recall)
    try:
        # Обычный режим: режим «полноты» тащит меню разделов и баннеры. Он — только запасной путь,
        # если обычный почти ничего не нашёл.
        xml_text = run(False)
        if not xml_text or words_count(re.sub(r"<[^>]+>", " ", xml_text)) < MIN_WORDS:
            xml_text = run(True) or xml_text
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
    # Сайт сам говорит, платная ли статья (schema.org isAccessibleForFree) — этому верим больше,
    # чем словам «оформите подписку», которые бывают в шапке любой страницы.
    m = re.search(r'isAccessibleForFree"?\s*:\s*"?(true|false)', html, re.I)
    free = None if m is None else m.group(1).lower() == "true"
    markers = any(mk in low for mk in PAYWALL_MARKERS)
    if out.words < MIN_WORDS:
        if any(mk in low for mk in CAPTCHA_MARKERS):
            out.status = "captcha"
        elif free is False or (free is None and markers):
            out.status = "paywall"
        else:
            out.status = "short"
    elif free is True:
        out.status = "ok"
    elif free is False:
        out.status = "paywall" if out.words < 600 else "ok"
    else:
        out.status = "paywall" if out.words < 350 and markers else "ok"
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
