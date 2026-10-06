"""Поиск по вашей библиотеке: SQLite FTS5 + русская и английская морфология (основы слов).

«нейросетей» находит «нейросеть», «регулированию» — «регулирование». Ранжирование:
релевантность × ваш вкус × свежесть. Фрагменты с подсветкой берутся из перевода или оригинала.
"""
from __future__ import annotations

import math
import re
import time

try:
    import Stemmer  # PyStemmer (Snowball)
    _RU = Stemmer.Stemmer("russian")
    _EN = Stemmer.Stemmer("english")
except ImportError:          # без PyStemmer поиск работает по словам целиком
    _RU = _EN = None

_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-+.#]*")
_CYR = re.compile(r"[А-Яа-яЁё]")


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е").strip("-.")
    if not w:
        return ""
    if _RU is None:
        return w
    return _RU.stemWord(w) if _CYR.search(w) else _EN.stemWord(w)


def stems(text: str) -> list[str]:
    return [s for s in (stem(w) for w in _WORD.findall(text or "")) if s]


def index_text(article: dict, blocks: list[dict]) -> str:
    parts = [article.get("title_ru", ""), article.get("title_orig", ""), " ".join(article.get("summary_ru") or []),
             article.get("topic", ""), article.get("subtopic", ""), " ".join(article.get("entities") or [])]
    for b in blocks:
        parts.append(b.get("text_ru") or "")
        parts.append(b.get("text_orig") or b.get("text") or "")
    return " ".join(stems(" ".join(parts)))


def reindex(storage, article_id: int) -> None:
    a = storage.article(article_id)
    if not a:
        return
    storage.index_article(article_id, index_text(a, storage.blocks(article_id)))


def _fts_query(query: str) -> str:
    terms = [s for s in stems(query) if len(s) >= 2]
    return " ".join(f'"{t}"*' for t in terms)


def search(storage, query: str, limit: int = 30, ranker=None) -> list[dict]:
    """Статьи по запросу: [{article, snippet, score}]."""
    q = _fts_query(query)
    if not q:
        return []
    rows = []
    if storage.fts_ok:
        try:
            rows = storage.query("SELECT rowid, bm25(fts) AS rank FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 200",
                                 (q,))
        except Exception:  # noqa: BLE001 — странный запрос не должен ронять поиск
            rows = []
    if not rows:     # запасной путь: поиск подстрокой по заголовкам
        like = f"%{query.strip()}%"
        rows = [{"rowid": r["id"], "rank": -1.0} for r in storage.query(
            "SELECT id FROM articles WHERE title_ru LIKE ? OR title_orig LIKE ? OR summary_ru LIKE ? LIMIT 200",
            (like, like, like))]
    now = time.time()
    out = []
    for r in rows:
        a = storage.article(r["rowid"])
        if not a or a["status"] == "hidden":
            continue
        relevance = -float(r["rank"])                    # bm25 у SQLite: меньше — лучше
        taste = 0.5
        if ranker is not None:
            taste = ranker.model.personal(a).score
        age_days = max(0.0, (now - float(a.get("collected_at") or now)) / 86400)
        score = relevance * (0.6 + 0.8 * taste) * (0.7 + 0.3 * math.exp(-age_days / 14))
        out.append({"article": a, "score": score, "snippet": snippet(storage, a, query)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def snippet(storage, article: dict, query: str, width: int = 220) -> str:
    """Фрагмент с совпадением: «…слова <b>совпадение</b> слова…»."""
    want = set(stems(query))
    texts = [" ".join(article.get("summary_ru") or [])]
    texts += [b.get("text_ru") or b.get("text_orig") or "" for b in storage.blocks(article["id"])]
    for text in texts:
        words = list(_WORD.finditer(text))
        for m in words:
            if stem(m.group()) in want:
                start = max(0, m.start() - width // 2)
                end = min(len(text), m.end() + width // 2)
                frag = text[start:end]
                for w in sorted({x.group() for x in _WORD.finditer(frag) if stem(x.group()) in want}, key=len,
                                reverse=True):
                    frag = re.sub(rf"(?<![\w>]){re.escape(w)}(?![\w<])", f"<b>{w}</b>", frag)
                return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")
    return (article.get("summary_ru") or [""])[0]
