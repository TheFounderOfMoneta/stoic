"""Мелкие общие функции: время, дни, JSON, склонения."""
from __future__ import annotations

import datetime as dt
import json
import re
import time

DAY = 86400.0


def now() -> float:
    return time.time()


def day_key(ts: float) -> str:
    """Локальная дата вида 2026-10-06 (день начинается в 4 утра: ночная учёба — это ещё «сегодня»)."""
    return dt.datetime.fromtimestamp(ts - 4 * 3600).strftime("%Y-%m-%d")


def day_start(ts: float) -> float:
    """Начало «дня» (4:00 местного времени), к которому относится момент ts."""
    d = dt.datetime.fromtimestamp(ts - 4 * 3600).date()
    return dt.datetime(d.year, d.month, d.day, 4, 0).timestamp()


def week_start(ts: float) -> float:
    """Понедельник 4:00 той недели, к которой относится ts."""
    start = day_start(ts)
    weekday = dt.datetime.fromtimestamp(start).weekday()
    return start - weekday * DAY


def jload(text, default):
    if text is None or text == "":
        return default
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def jdump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ("день", "дня", "дней")) → «дней»."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def minutes_text(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return f"{m} мин"
    h, rest = divmod(m, 60)
    return f"{h} ч {rest} мин" if rest else f"{h} ч"


def slugify(text: str) -> str:
    """Короткий ключ понятия: латиница/кириллица, цифры и дефисы."""
    s = re.sub(r"[^0-9a-zа-яё]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "ponyatie"


def when_text(ts: float, now_ts: float | None = None) -> str:
    """«сегодня в 19:10», «вчера в 08:02», «3 окт.»."""
    now_ts = now_ts or time.time()
    t = time.localtime(ts)
    if day_key(ts) == day_key(now_ts):
        return f"сегодня в {time.strftime('%H:%M', t)}"
    if day_key(ts + DAY) == day_key(now_ts):
        return f"вчера в {time.strftime('%H:%M', t)}"
    months = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."]
    return f"{t.tm_mday} {months[t.tm_mon - 1]}"
