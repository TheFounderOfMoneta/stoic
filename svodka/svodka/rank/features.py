"""Признаки статьи — то, по чему лента учится вашим вкусам.

Каждая статья раскладывается на признаки разных типов: тема, подтема, герои (компании,
люди, страны, продукты), источник, тип материала, длина. Ваши действия со статьёй
распределяются по её признакам, и у каждого признака копится своя статистика.
"""
from __future__ import annotations

from ..util import norm_entity

# Вклад типа признака в персональный прогноз (сумма не обязана быть 1 — нормируется).
TYPE_WEIGHTS = {
    "topic": 0.25,
    "sub": 0.18,
    "entity": 0.20,
    "source": 0.15,
    "kind": 0.14,
    "len": 0.08,
}
CONTENT_TYPES = frozenset({"topic", "sub", "entity", "kind", "len"})
SOURCE_TYPES = frozenset({"source"})
ALL_TYPES = frozenset(TYPE_WEIGHTS)

KINDS = {
    "news": "новости",
    "analysis": "разборы",
    "research": "исследования",
    "release": "релизы",
    "policy": "решения и законы",
    "interview": "интервью",
    "opinion": "мнения",
    "tutorial": "практика",
    "data": "данные и цифры",
    "rumor": "слухи",
}


def length_bucket(words: int) -> str:
    if words <= 0:
        return ""
    if words < 600:
        return "short"
    if words < 1800:
        return "medium"
    return "long"


def article_features(a: dict) -> list[str]:
    """Признаки статьи вида «тип:значение»."""
    feats: list[str] = []
    topic = (a.get("topic") or "").strip()
    if topic:
        feats.append("topic:" + topic)
    sub = norm_entity(a.get("subtopic") or "")
    if sub:
        feats.append("sub:" + sub)
    seen = set()
    for e in (a.get("entities") or [])[:3]:
        key = norm_entity(str(e))
        if key and key not in seen:
            seen.add(key)
            feats.append("entity:" + key)
    domain = (a.get("domain") or "").strip().lower()
    if domain:
        feats.append("source:" + domain)
    kind = (a.get("kind") or "").strip().lower()
    if kind:
        feats.append("kind:" + kind)
    bucket = length_bucket(int(a.get("words") or 0))
    if bucket:
        feats.append("len:" + bucket)
    return feats


def ftype(feature: str) -> str:
    return feature.split(":", 1)[0]


def fvalue(feature: str) -> str:
    return feature.split(":", 1)[1] if ":" in feature else feature


def human(feature: str, display: dict | None = None) -> str:
    """Признак по-человечески: для сводки Claude и для экрана «Качество ленты»."""
    t, v = ftype(feature), fvalue(feature)
    shown = (display or {}).get(feature, v)
    return {
        "topic": f"тема «{shown}»",
        "sub": f"подтема «{shown}»",
        "entity": f"герой «{shown}»",
        "source": f"источник {shown}",
        "kind": f"тип «{KINDS.get(v, v)}»",
        "len": {"short": "короткие тексты", "medium": "тексты средней длины", "long": "длинные тексты"}.get(v, v),
    }.get(t, feature)
