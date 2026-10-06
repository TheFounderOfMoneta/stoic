"""Сводка для Claude перед сбором: что вам нравится, что нет, что уже было.

Claude — это «этап кандидатов»: он ищет и отбирает статьи по квотам. Чем точнее он знает,
что вам заходит (и почему), тем лучше кандидаты. Сюда попадает только нужное для поиска:
темы, профиль, сильные и слабые признаки, примеры удачных и неудачных статей, правила.
"""
from __future__ import annotations

import time

from .features import ALL_TYPES, human
from .model import InterestModel


def _reaction_text(sig) -> str:
    r = sig.reactions
    parts = []
    if sig.implicit.get("read_score", 0) >= 0.6:
        parts.append("дочитал")
    for key, text in (("superlike", "очень круто"), ("like", "интересно"), ("save", "сохранил"),
                      ("follow", "следит за сюжетом"), ("dislike", "не моя тема"), ("known", "уже знал"),
                      ("shallow", "поверхностно"), ("clickbait", "кликбейт")):
        if r.get(key):
            parts.append(text)
    if sig.implicit.get("ask"):
        parts.append("задал вопрос")
    if sig.implicit.get("short_click"):
        parts.append("открыл и сразу закрыл")
    if sig.stars is not None:
        parts.append(f"оценка ★{sig.stars}")
    return ", ".join(parts)


def build_brief(storage, settings=None, now: float | None = None, kind: str = "collect",
                query: str = "", model: InterestModel | None = None) -> dict:
    now = now if now is not None else time.time()
    get = (lambda k, d: settings.get(k, d)) if settings is not None else (lambda k, d: d)
    model = model or InterestModel.build(storage, now=now)
    n_total = int(get("collect.articles_per_run", 15)) if kind == "collect" else 8
    q_core = float(get("collect.quota_core", 0.7))
    q_explore = float(get("collect.quota_explore", 0.2))
    n_explore = max(1, round(n_total * q_explore)) if kind == "collect" else 0
    n_world = max(1, round(n_total * float(get("collect.quota_world", 0.1)))) if kind == "collect" else 0
    n_core = max(1, n_total - n_explore - n_world) if q_core > 0 else 0

    def feat_list(items):
        return [{"признак": human(f, model.display), "насколько нравится": round(m, 2),
                 "уверенность": round(min(1.0, ev / 10.0), 2)} for f, m, ev in items]

    liked = model.top_features(positive=True, limit=14, min_evidence=1.0, types=ALL_TYPES)
    disliked = model.top_features(positive=False, limit=10, min_evidence=1.0, types=ALL_TYPES)

    valued, rejected = [], []
    recent = sorted(((sig, model.articles.get(aid)) for aid, sig in model.signals.items()),
                    key=lambda x: max((c.ts for c in x[0].contribs), default=0), reverse=True)
    for sig, a in recent:
        if not a or not sig.contribs:
            continue
        title = a.get("title_ru") or a.get("title_orig") or ""
        if sig.valuable and len(valued) < 8:
            valued.append({"заголовок": title, "тема": a.get("topic", ""), "что сделал": _reaction_text(sig)})
        elif sig.rejected and len(rejected) < 8:
            rejected.append({"заголовок": title, "тема": a.get("topic", ""), "что сделал": _reaction_text(sig)})
        if len(valued) >= 8 and len(rejected) >= 8:
            break

    rules = [{"скрыть": {"mute_source": "источник", "mute_topic": "тему", "mute_entity": "героя"}.get(r["kind"],
              r["kind"]), "что": r["target"]} for r in storage.active_rules()]
    recent_titles = [r["t"] for r in storage.query(
        "SELECT COALESCE(NULLIF(title_orig, ''), title_ru) AS t FROM articles WHERE collected_at >= ? "
        "ORDER BY collected_at DESC LIMIT 120", (now - 3 * 86400,))]
    followed = [{"сюжет": r["title_ru"] or r["title_orig"], "ключ": r["story_key"]} for r in storage.query(
        "SELECT title_ru, title_orig, story_key FROM articles WHERE followed=1 AND collected_at >= ? "
        "ORDER BY collected_at DESC LIMIT 10", (now - 14 * 86400,))]

    brief = {
        "задача": "сбор" if kind == "collect" else "поиск по запросу",
        "сейчас": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "темы": [{"название": t["name"], "описание": t["description"], "вес": t["weight"],
                  "обязательные слова": t["include_words"], "исключить": t["exclude_words"]}
                 for t in storage.topics()],
        "профиль": storage.profile_text(),
        "сколько статей": {"всего": n_total, "интересы": n_core, "разведка": n_explore, "главное в мире": n_world},
        "что нравится": feat_list(liked),
        "что не нравится": feat_list(disliked),
        "зашло недавно": valued,
        "не зашло недавно": rejected,
        "правила": rules,
        "следит за сюжетами": followed,
        "уже было за 3 дня (не повторять)": recent_titles,
        "сохранённые запросы": [q["text"] for q in storage.saved_queries()],
        "уверенность модели": round(model.confidence, 2),
    }
    if kind == "search":
        brief["запрос"] = query
    return brief
