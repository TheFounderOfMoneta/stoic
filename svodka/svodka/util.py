"""Мелкие общие функции: нормализация ссылок, язык текста, даты."""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Параметры слежки, которые не меняют страницу: одна статья — один ключ.
_TRACKING = {
    "gclid", "fbclid", "msclkid", "yclid", "ocid", "sc_cid", "mc_cid", "mc_eid", "igshid",
    "ref", "ref_src", "ref_url", "cmpid", "smid", "guccounter", "_ga", "share", "s",
}


def normalize_url(url: str) -> str:
    """Ключ статьи: без схемы, www, якоря, слежки (utm_* и пр.) и хвостового «/»."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url if "://" in url else "https://" + url)
    except ValueError:
        return url.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    query.sort()
    return urlunsplit(("", host, path, urlencode(query), "")).lstrip("/")


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


def detect_lang(text: str) -> str:
    """Грубо: «ru», если кириллицы больше латиницы, иначе «en» (для решения «переводить ли»)."""
    sample = (text or "")[:4000]
    cyr, lat = len(_CYR.findall(sample)), len(_LAT.findall(sample))
    if cyr == 0 and lat == 0:
        return ""
    return "ru" if cyr >= lat else "en"


def words_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def now() -> float:
    return time.time()


def day_key(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def parse_date(value) -> float | None:
    """ISO-дата/время в метку времени; None — если не разобрать."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            d = dt.datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.astimezone()     # без пояса — считаем местным временем
    return d.timestamp()


def jdump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def jload(text, default):
    if text is None or text == "":
        return default
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def norm_entity(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()[:80]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x
