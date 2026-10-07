"""Ранжирование ленты в три этапа (как у YouTube: кандидаты → оценка → разнообразие).

1. Кандидаты собирает Claude по квотам: ваши интересы / разведка / главное в мире.
2. Оценка каждой статьи — смесь четырёх частей (все в шкале 0–1):
     персональный прогноз (выборка Томпсона) · соответствие профилю (оценка Claude) ·
     важность события в мире · свежесть.
   Пока о вас мало знаем, больше веса у профиля и свежести; чем больше действий —
   тем больше решает персональная модель.
3. Разнообразие и разведка: в «Главном» не больше одной статьи на сюжет и двух на героя,
   штраф за однотипность, 1–2 места под «Разведку»; иногда две верхние статьи меняются местами,
   чтобы честно измерять влияние позиции.

У каждой статьи — понятные причины «почему здесь».
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from ..util import clamp, day_key, norm_entity
from .features import KINDS, ftype, fvalue
from .mixing import learn_mix
from .model import InterestModel, Personal, day_rng

MAIN_SIZE = 5
RECENT_HOURS = 48
FRESH_HOURS = 72          # «Главное» и «Ещё свежее» — только опубликованное за последние 3 дня
FEED_DAYS = 7


@dataclass
class Item:
    article: dict
    score: float
    parts: dict
    weights: dict
    personal: Personal
    raw: dict = field(default_factory=dict)       # части до нормировки (для объяснений)
    reasons: list = field(default_factory=list)
    explore: bool = False
    novel_topic: bool = False      # разведка за пределами названных вами тем (значок в ленте)
    randomized: bool = False

    @property
    def id(self) -> int:
        return self.article["id"]


@dataclass
class Feed:
    main: list
    more: list
    days: list           # [(ключ дня, [Item])]
    confidence: float
    built_at: float
    randomized: bool = False

    def all_items(self) -> list:
        out = list(self.main) + list(self.more)
        for _day, items in self.days:
            out += items
        return out


def mix_weights(confidence: float) -> dict:
    c = clamp(confidence)
    return {
        "personal": 0.15 + 0.40 * c,
        "fit": 0.45 - 0.20 * c,
        "importance": 0.15 - 0.05 * c,
        "fresh": 0.25 - 0.15 * c,
    }


def freshness(article: dict, now: float) -> float:
    ts = article.get("published_at") or article.get("collected_at") or now
    age_h = max(0.0, (now - float(ts)) / 3600.0)
    return math.exp(-age_h / 36.0)


def rule_blocks(article: dict, rules: list[dict]) -> bool:
    """Жёсткие правила (скрыть тему / источник / героя) сильнее любого обучения."""
    domain = (article.get("domain") or "").lower()
    topic = article.get("topic") or ""
    ents = {norm_entity(str(e)) for e in article.get("entities") or []}
    for r in rules:
        target = (r.get("target") or "").strip()
        if r["kind"] == "mute_source" and target.lower() == domain:
            return True
        if r["kind"] == "mute_topic" and target == topic:
            return True
        if r["kind"] == "mute_entity" and norm_entity(target) in ents:
            return True
    return False


class Ranker:
    def __init__(self, storage, settings=None, now: float | None = None, model: InterestModel | None = None):
        self.storage = storage
        self.settings = settings
        self.now = now if now is not None else time.time()
        self.model = model or InterestModel.build(storage, now=self.now)
        self.personalization = self._setting("learning.personalization", True)
        self.weights = mix_weights(self.model.confidence)
        self.mix_report: dict = {}
        if self.personalization:
            # веса частей подстраиваются под вас по истории показов (rank/mixing.py)
            self.weights, self.mix_report = learn_mix(storage, self.model.signals, self.weights, now=self.now)
        self.draw = self.model.draw(day_rng("feed", self.now))
        self.rules = storage.active_rules()
        # Темы, которые вы назвали сами, — не «разведка», даже пока по ним мало данных.
        self.declared = {t["name"] for t in storage.topics()}
        self.followed_stories = {r["story_key"] for r in storage.query(
            "SELECT story_key FROM articles WHERE followed=1 AND story_key != '' AND collected_at >= ?",
            (self.now - 14 * 86400,))}

    def _setting(self, key: str, default):
        return self.settings.get(key, default) if self.settings is not None else default

    # ------------------------------------------------------------------ оценка
    def _raw(self, article: dict, sample: bool) -> tuple[Personal, dict]:
        if self.personalization:
            p = self.model.personal(article, self.draw if sample else None)
        else:
            p = Personal(score=0.5, detail=[], novelty=0.0)
        raw = {
            "personal": p.score,
            "fit": clamp(float(article.get("fit") or 0.5)),
            "importance": clamp(float(article.get("importance") or 0.5)),
            "fresh": freshness(article, self.now),
        }
        return p, raw

    def score_many(self, articles: list[dict], sample: bool = True) -> list[Item]:
        """Оценка набора статей. Каждая часть переводится в процентиль внутри набора: так вес
        части — это её реальное влияние на порядок, а не случайность её разброса."""
        raws = [self._raw(a, sample) for a in articles]
        keys = ("personal", "fit", "importance", "fresh")
        if len(raws) >= 4:
            norm = {k: percentiles([r[k] for _p, r in raws]) for k in keys}
        else:
            norm = {k: [r[k] for _p, r in raws] for k in keys}
        items = []
        for i, (a, (p, raw)) in enumerate(zip(articles, raws)):
            parts = {k: norm[k][i] for k in keys}
            bonus = 0.0
            if a.get("story_key") and a["story_key"] in self.followed_stories and not a.get("followed"):
                bonus += 0.08
            parts["bonus"] = bonus
            total = sum(self.weights[k] * parts[k] for k in keys) + bonus
            item = Item(article=a, score=total, parts=parts, weights=dict(self.weights), personal=p, raw=raw)
            # Разведка — только то, о чём мы правда мало знаем. Изученное (даже если Claude принёс
            # его как «разведку») оценивается на общих основаниях.
            novelty_cut = 0.3 if a.get("bucket") == "explore" else 0.5
            item.explore = bool(self.personalization and p.novelty >= novelty_cut) or \
                (not self.personalization and a.get("bucket") == "explore")
            # Значок «Разведка» — только для тем, которых вы не называли (по своим темам это просто
            # «пока мало знаем»). На ранжирование это не влияет: симуляция показала, что так лента
            # лучше находит новые интересы и быстрее перестраивается.
            item.novel_topic = item.explore and a.get("topic") not in self.declared
            item.reasons = self.reasons(item)
            items.append(item)
        return items

    def score(self, article: dict, sample: bool = True) -> Item:
        return self.score_many([article], sample)[0]

    def reasons(self, item: Item) -> list[str]:
        a, parts, raw = item.article, item.parts, item.raw
        out: list[str] = []
        if parts.get("bonus", 0) > 0:
            out.append("продолжение сюжета, за которым вы следите")
        if a.get("query"):
            out.append(f"по вашему запросу «{a['query']}»")
        if self.personalization:
            liked = []
            for f, _v, _w in item.personal.detail:
                st = self.model.stats.get(f)
                if not st:
                    continue
                mean = st.mean()
                if st.n_pos >= 2 and mean >= 0.62:
                    liked.append((mean * min(4, st.n_pos), f, st.n_pos))
            liked.sort(reverse=True)
            for _k, f, n in liked[:2]:
                t, v = ftype(f), fvalue(f)
                shown = self.model.display.get(f, v)
                if t == "topic":
                    out.append(f"вы часто читаете «{shown}» ({n})")
                elif t == "entity":
                    out.append(f"вам интересно: {shown}")
                elif t == "sub":
                    out.append(f"близко к тому, что вы дочитываете: {shown}")
                elif t == "source":
                    out.append("источник, которому вы доверяете")
                elif t == "kind":
                    out.append(f"вам заходят {KINDS.get(v, v)}")
                elif t == "len":
                    out.append("длинные тексты вам заходят" if v == "long" else "короткие тексты вам заходят")
        if item.explore:
            out.append("разведка: новое для вас — оцените" if item.novel_topic
                       else "пока мало знаем, как вам такое — оцените")
        if raw.get("fit", 0) >= 0.75 and len(out) < 3:
            out.append("совпадает с вашим профилем")
        if raw.get("importance", 0) >= 0.75 and len(out) < 3:
            out.append("важное событие в мире")
        published = a.get("published_at") or a.get("collected_at")
        if published and self.now - float(published) < 6 * 3600 and len(out) < 3:
            out.append("свежая")
        if not out and a.get("topic"):
            out.append(f"по теме «{a['topic']}»")
        return out[:3]

    # ------------------------------------------------------------------ лента
    def build(self) -> Feed:
        rows = self.storage.articles("purged=0 AND status != 'hidden' AND collected_at >= ?",
                                     (self.now - FEED_DAYS * 86400,))
        rows = [a for a in rows if not rule_blocks(a, self.rules)]
        items = self.score_many(rows)
        recent_cut = self.now - RECENT_HOURS * 3600
        max_age = float(self._setting("collect.max_age_hours", FRESH_HOURS)) * 3600
        fresh_new = [it for it in items if it.article["status"] == "new" and it.article["collected_at"] >= recent_cut
                     and self.now - float(it.article.get("published_at") or it.article["collected_at"]) <= max_age]
        main, rest = diversify(fresh_new, MAIN_SIZE, story_cap=1, entity_cap=2)
        main = self._ensure_exploration(main, rest)
        main_ids = {it.id for it in main}
        rest = [it for it in fresh_new if it.id not in main_ids]
        more, _ = diversify(rest, len(rest), story_cap=2, entity_cap=4)
        randomized = False
        prob = float(self._setting("learning.randomize_top_prob", 0.1))
        if len(main) >= 2 and day_rng("swap", self.now).random() < prob:
            main[0], main[1] = main[1], main[0]
            main[0].randomized = main[1].randomized = True
            randomized = True
        shown = {it.id for it in main} | {it.id for it in more}
        others = [it for it in items if it.id not in shown]
        by_day: dict[str, list] = {}
        for it in others:
            by_day.setdefault(day_key(it.article["collected_at"]), []).append(it)
        days = []
        for day in sorted(by_day, reverse=True):
            group = by_day[day]
            group.sort(key=lambda it: (it.article["status"] != "new", -it.score))
            days.append((day, group))
        return Feed(main=main, more=more, days=days, confidence=self.model.confidence, built_at=self.now,
                    randomized=randomized)

    def _explore_slots(self) -> int:
        created = float(self.storage.meta_get("created_at", "0") or 0)
        first_week = created and self.now - created < 7 * 86400
        key = "learning.explore_slots_first_week" if first_week else "learning.explore_slots"
        return int(self._setting(key, 2 if first_week else 1))

    def _ensure_exploration(self, main: list, rest: list) -> list:
        need = self._explore_slots() - sum(1 for it in main if it.explore)
        if need <= 0 or not main:
            return main
        # оптимистичная (выборка Томпсона) оценка не должна быть совсем низкой — не тратим место впустую
        candidates = sorted((it for it in rest if it.explore and it.parts.get("personal", 0) >= 0.25),
                            key=lambda it: it.score, reverse=True)
        main = list(main)
        stories = {it.article.get("story_key") for it in main if it.article.get("story_key")}
        candidates = [c for c in candidates if not c.article.get("story_key") or c.article["story_key"] not in stories]
        for cand in candidates[:need]:
            # заменяем самую слабую «обычную» статью, но не первые две
            replace = [i for i in range(len(main) - 1, 1, -1) if not main[i].explore]
            if replace:
                main[replace[0]] = cand
            elif len(main) < MAIN_SIZE:
                main.append(cand)
        # разведку ставим не выше третьей позиции
        head = [it for it in main[:2]]
        tail = sorted(main[2:], key=lambda it: it.score, reverse=True)
        return head + tail

    # ------------------------------------------------------------------ похожее
    def similar(self, article: dict, limit: int = 3) -> list:
        ents = {norm_entity(str(e)) for e in article.get("entities") or []}
        rows = self.storage.articles("purged=0 AND status != 'hidden' AND id != ? AND collected_at >= ?",
                                     (article["id"], self.now - 30 * 86400))
        pool, sims = [], []
        for a in rows:
            if rule_blocks(a, self.rules):
                continue
            sim = 0.0
            if a.get("story_key") and a["story_key"] == article.get("story_key"):
                sim += 1.0
            sim += 0.4 * len(ents & {norm_entity(str(e)) for e in a.get("entities") or []})
            if a.get("topic") == article.get("topic"):
                sim += 0.25
            if a.get("subtopic") and a.get("subtopic") == article.get("subtopic"):
                sim += 0.3
            if sim > 0.25:
                pool.append(a)
                sims.append(sim)
        items = self.score_many(pool, sample=False)
        ranked = sorted(zip(sims, items), key=lambda x: x[0] + x[1].score, reverse=True)
        return [it for _s, it in ranked[:limit]]


def percentiles(values: list[float]) -> list[float]:
    """Процентиль каждого значения в наборе (0 — худшее, 1 — лучшее; равные — поровну)."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = rank
        i = j + 1
    return out


def diversify(items: list, k: int, story_cap: int = 1, entity_cap: int = 2) -> tuple[list, list]:
    """Жадный отбор с штрафом за похожесть (упрощённый аналог DPP YouTube)."""
    pool = sorted(items, key=lambda it: it.score, reverse=True)
    chosen: list = []
    stories: dict[str, int] = {}
    ents: dict[str, int] = {}
    topics: dict[str, int] = {}
    while pool and len(chosen) < k:
        best, best_adj = None, -1e9
        for it in pool:
            a = it.article
            story = a.get("story_key") or ""
            if story and stories.get(story, 0) >= story_cap:
                continue
            item_ents = {norm_entity(str(e)) for e in a.get("entities") or []}
            if any(ents.get(e, 0) >= entity_cap for e in item_ents):
                continue
            penalty = 0.03 * sum(ents.get(e, 0) for e in item_ents)
            penalty += 0.03 * max(0, topics.get(a.get("topic") or "", 0) - 1)
            adj = it.score - penalty
            if adj > best_adj:
                best, best_adj = it, adj
        if best is None:
            break
        chosen.append(best)
        pool.remove(best)
        a = best.article
        if a.get("story_key"):
            stories[a["story_key"]] = stories.get(a["story_key"], 0) + 1
        for e in {norm_entity(str(e)) for e in a.get("entities") or []}:
            ents[e] = ents.get(e, 0) + 1
        topics[a.get("topic") or ""] = topics.get(a.get("topic") or "", 0) + 1
    return chosen, pool
