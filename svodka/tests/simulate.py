"""Симулированный читатель: проверяем, что лента действительно учится.

У читателя скрытые вкусы (похожие на владельца «Сводки»): ИИ и новые технологии, внутреннее
устройство Китая/США/России, разборы и исследования, длинные тексты; не любит слухи и
кликбейтные сайты; тайно интересуется биотехом (это должна найти разведка) и не любит спорт.
Каждый «день» приходит 15 новых статей, читатель листает ленту как человек: верх видит почти
всегда, низ — реже; открывает то, что цепляет; кликбейт открывает, но сразу закрывает;
иногда ставит реакции и отвечает на опрос.

Мера качества — доля по-настоящему ценных статей в «Главном» (топ-5) по скрытой правде.

Запуск вручную: python -m tests.simulate  — печатает кривую обучения.
"""
from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

from svodka.rank.model import InterestModel
from svodka.rank.ranker import Ranker
from svodka.rank.signals import expected_ms
from svodka.storage import SEED_TOPICS, Storage

DAY = 86400.0
START = 1_790_000_000.0          # условное «сегодня» симуляции

DECLARED = [name for name, _d, _w in SEED_TOPICS]
EXPLORE_TOPICS = ["Биотех", "Спорт", "Криптовалюты", "Космос"]

ENTITIES = {
    "ИИ: развитие": ["OpenAI", "Anthropic", "DeepMind", "Mistral", "Nvidia", "Meta AI", "Qwen"],
    "Новые технологии": ["TSMC", "SpaceX", "ITER", "Samsung", "ASML", "CATL"],
    "Китай: внутренняя политика": ["Госсовет КНР", "НПК", "ЦК КПК", "Си Цзиньпин", "провинция Гуандун"],
    "США: внутренняя политика": ["Конгресс", "Верховный суд", "FTC", "Минторг США", "штат Калифорния"],
    "Россия: внутренняя политика": ["Госдума", "Правительство РФ", "ЦБ РФ", "Минцифры", "регионы"],
    "Внешняя политика Китая, США и России": ["ООН", "БРИКС", "НАТО", "G20"],
    "Биотех": ["CRISPR", "Moderna", "AlphaFold", "Illumina"],
    "Спорт": ["ФИФА", "НБА", "Олимпиада"],
    "Криптовалюты": ["Bitcoin", "Ethereum", "Binance"],
    "Космос": ["NASA", "Роскосмос", "ESA"],
}
SOURCES = {   # домен → качество для читателя (кликбейт — сильно отрицательное)
    "systems-review.org": 0.6, "tech-analysis.io": 0.5, "policy-notes.com": 0.5, "research-digest.net": 0.4,
    "daily-tech.com": 0.1, "newswire.example": 0.0, "world-report.org": 0.1, "habr.example": 0.3,
    "hype-news.biz": -1.0, "shock-headlines.com": -1.0, "viral-ai.net": -1.0, "opinion-hub.com": -0.2,
}
CLICKBAIT = {d for d, q in SOURCES.items() if q <= -1.0}
KIND_PREF = {"analysis": 0.45, "research": 0.4, "policy": 0.35, "release": 0.1, "news": 0.0,
             "interview": 0.1, "opinion": -0.15, "rumor": -0.7}


class Persona:
    def __init__(self, seed: int = 1):
        rng = random.Random(seed)
        self.topic_pref = {
            "ИИ: развитие": 0.9, "Новые технологии": 0.7,
            "Китай: внутренняя политика": 0.6, "США: внутренняя политика": 0.55,
            "Россия: внутренняя политика": 0.55, "Внешняя политика Китая, США и России": 0.05,
            "Биотех": 0.75, "Спорт": -0.8, "Криптовалюты": -0.5, "Космос": 0.1,
        }
        self.entity_pref = {e: rng.uniform(-0.3, 0.4) for ents in ENTITIES.values() for e in ents}
        self.entity_pref.update({"Anthropic": 0.6, "Госсовет КНР": 0.5, "Binance": -0.6})
        self.long_pref = 0.2

    def utility(self, a: dict, noise: float) -> float:
        ents = a["entities"]
        u = self.topic_pref.get(a["topic"], 0.0)
        u += 0.5 * (sum(self.entity_pref.get(e, 0.0) for e in ents) / max(1, len(ents)))
        u += 0.5 * SOURCES.get(a["domain"], 0.0)
        u += KIND_PREF.get(a["kind"], 0.0)
        u += self.long_pref if a["words"] >= 1800 else 0.0
        return u + noise

    def shift(self) -> None:
        """Интересы меняются: охладел к США, увлёкся космосом."""
        self.topic_pref["США: внутренняя политика"] = -0.3
        self.topic_pref["Космос"] = 0.9


VALUABLE = 1.0     # порог «по-настоящему ценно» по скрытой полезности


class SimSettings:
    def __init__(self, **overrides):
        self.values = dict(overrides)

    def get(self, key, default=None):
        return self.values.get(key, default)


def make_articles(rng: random.Random, day: int, n: int = 15) -> list[dict]:
    """Кандидаты дня, как их принёс бы Claude: в основном по темам, немного разведки."""
    out = []
    stories = [f"story-{day}-{i}" for i in range(4)]
    for i in range(n):
        explore = rng.random() < 0.2
        topic = rng.choice(EXPLORE_TOPICS) if explore else rng.choice(DECLARED)
        ents = rng.sample(ENTITIES[topic], k=min(2, len(ENTITIES[topic])))
        domain = rng.choice(list(SOURCES))
        kind = rng.choice(list(KIND_PREF))
        words = rng.choice([400, 900, 1500, 2400, 3500])
        declared_w = next((w for name, _d, w in SEED_TOPICS if name == topic), 0.3)
        out.append({
            "url": f"https://{domain}/{day}/{i}",
            "title_ru": f"[{topic}] {kind} про {', '.join(ents)} ({day}.{i})",
            "title_orig": f"{topic} {kind} {day}.{i}",
            "topic": topic, "subtopic": "", "entities": ents, "domain": domain, "source": domain,
            "kind": kind, "words": words, "bucket": "explore" if explore else "core",
            # Claude видит только заявленный профиль, не скрытые вкусы: оценка шумная
            "fit": max(0.0, min(1.0, 0.35 + 0.4 * declared_w + rng.gauss(0, 0.12))),
            "importance": rng.uniform(0.2, 0.9),
            "story_key": rng.choice(stories) if rng.random() < 0.3 else "",
            "lang": "en", "extract_status": "ok",
        })
    return out


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def simulate(days: int = 21, personalization: bool = True, seed: int = 7, shift_day: int | None = None,
             db_dir: Path | None = None, oracle: bool = False) -> dict:
    tmp = Path(db_dir or tempfile.mkdtemp(prefix="svodka-sim-"))
    storage = Storage(tmp / f"sim-{seed}-{int(personalization)}.sqlite3")
    storage.meta_set("created_at", str(START))
    persona = Persona(seed)
    rng = random.Random(seed)
    settings = SimSettings(**{"learning.personalization": personalization, "learning.randomize_top_prob": 0.1})
    precision, explore_in_main, story_violations = [], [], 0
    truth: dict[int, float] = {}       # скрытая полезность всех статей (вчерашние тоже бывают в «Главном»)
    for day in range(days):
        if shift_day is not None and day == shift_day:
            persona.shift()
        t0 = START + day * DAY
        for a in make_articles(rng, day):
            a["collected_at"] = t0 + 60
            a["published_at"] = t0 - rng.uniform(0, 20) * 3600
            aid = storage.add_article(a)
            truth[aid] = persona.utility(a, rng.gauss(0, 0.25))
        now = t0 + 3600
        ranker = Ranker(storage, settings, now=now)
        feed = ranker.build()
        order = feed.main + feed.more
        if oracle:
            order = sorted(order, key=lambda it: truth.get(it.id, -9), reverse=True)
        main = order[:5]
        precision.append(sum(1 for it in main if truth.get(it.id, -9) >= VALUABLE) / max(1, len(main)))
        explore_in_main.append(sum(1 for it in feed.main if it.explore))
        stories = [it.article.get("story_key") for it in feed.main if it.article.get("story_key")]
        story_violations += len(stories) - len(set(stories))
        # --- читатель листает ленту ---
        ts = now + 60
        for pos, it in enumerate(order):
            if rng.random() > 1 / (1 + pos / 3):      # не долистал / не разглядел
                continue
            ts += 20
            aid, a, u = it.id, it.article, truth.get(it.id, 0.0)
            storage.log_impression(aid, pos, 2500, randomized=it.randomized, ts=ts, parts=it.parts)
            bait = a["domain"] in CLICKBAIT
            p_open = sigmoid(3.0 * ((u + (1.2 if bait else 0.0)) - 0.7))
            if rng.random() < p_open:
                exp = expected_ms(a["words"])
                storage.log_event(aid, "open", ts=ts)
                if bait or u < 0.25:
                    storage.log_read(aid, int(exp * 0.06), 0.08, exp, ts=ts + 5)
                    if bait and rng.random() < 0.3:
                        storage.log_event(aid, "clickbait", 1, ts=ts + 6)
                    continue
                ratio = max(0.2, min(1.3, 0.25 + 0.55 * u))
                scroll = max(0.2, min(1.0, 0.35 + 0.5 * u))
                storage.log_read(aid, int(exp * ratio), scroll, exp, ts=ts + 30)
                storage.update_article(aid, status="read")
                if u >= 1.1 and rng.random() < 0.35:
                    storage.log_event(aid, "like", 1, ts=ts + 31)
                if u >= 1.5 and rng.random() < 0.15:
                    storage.log_event(aid, "superlike", 1, ts=ts + 32)
                if u >= 1.2 and rng.random() < 0.1:
                    storage.log_event(aid, "save", 1, ts=ts + 33)
                if rng.random() < 0.25:
                    stars = round(max(1, min(5, 3 + 1.6 * (u - 0.7))))
                    storage.log_survey(aid, stars, ts=ts + 34)
            else:
                if u < 0.0 and rng.random() < 0.2:
                    storage.log_event(aid, "dislike", 1, ts=ts + 3)
                elif 0.4 < u < 0.9 and rng.random() < 0.15:
                    storage.log_event(aid, "expand", ts=ts + 3)
    model = InterestModel.build(storage, now=START + days * DAY)
    storage.close()
    return {"precision": precision, "explore_in_main": explore_in_main, "story_violations": story_violations,
            "model": model, "truth": truth, "persona": persona, "rng": rng, "db": storage.path}


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


if __name__ == "__main__":
    for label, kwargs in (("персонализация", {}), ("без персонализации", {"personalization": False}),
                          ("идеал (знает правду)", {"oracle": True})):
        runs = [simulate(days=28, seed=s, **kwargs) for s in (3, 7, 11)]
        curve = [mean([r["precision"][d] for r in runs]) for d in range(28)]
        weeks = [round(mean(curve[i:i + 7]), 2) for i in range(0, 28, 7)]
        print(f"{label:22s} доля ценного в топ-5 по неделям: {weeks}")
