"""План на сегодня: повторения, у которых подошёл срок, и новое понятие — в пределах вашего времени.

Время берётся из ваших же данных: сколько у вас обычно уходит на карточку и на новое понятие,
и через сколько минут сессии точность начинает падать. В тяжёлый день план вдвое короче,
после пропуска — короткая сессия-возврат на 5 минут вместо долга из всех повторений.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..util import DAY, day_start, now as _now
from . import metrics

DEFAULT_REVIEW_MIN = 0.6
DEFAULT_CONCEPT_MIN = 15.0
RETURN_MINUTES = 5


@dataclass
class Plan:
    kind: str = "normal"                  # normal | short | return | empty
    topic_id: int | None = None
    reviews: list = field(default_factory=list)
    concept: dict | None = None
    minutes: float = 0.0
    budget: float = 0.0
    review_min: float = DEFAULT_REVIEW_MIN
    note: str = ""
    open_loop: str = ""
    due_total: int = 0

    def summary(self) -> str:
        parts = []
        if self.reviews:
            parts.append(f"повторений: {len(self.reviews)}")
        if self.concept:
            parts.append(f"новое: «{self.concept['title']}»")
        if not parts:
            return "на сегодня всё сделано"
        return " · ".join(parts) + f" · ~{max(1, round(self.minutes))} мин"


def review_minutes(storage) -> float:
    rows = storage.query("SELECT latency_ms FROM attempts WHERE phase IN ('review','delayed') AND latency_ms>0 "
                         "ORDER BY ts DESC LIMIT 200")
    if len(rows) < 10:
        return DEFAULT_REVIEW_MIN
    # время ответа плюс ~10 секунд на оценку и переход
    return min(5.0, statistics.median(r["latency_ms"] for r in rows) / 60000 + 0.17)


def concept_minutes(storage) -> float:
    vals = []
    for s in storage.sessions(kind="learn", finished=True)[-30:]:
        n = storage.one("SELECT count(*) AS n FROM concepts WHERE intro_session=?", (s["id"],))["n"]
        if n and s["active_ms"]:
            vals.append(s["active_ms"] / 60000 / n)
    return statistics.median(vals) if len(vals) >= 3 else DEFAULT_CONCEPT_MIN


def next_concept(storage, topic_id: int) -> dict | None:
    """Новое понятие, у которого все предварительные уже начаты. Порядок карты, при равенстве — интерес."""
    concepts = storage.concepts(topic_id)
    started = {c["slug"] for c in concepts if c["status"] != "new"}
    known = {c["slug"] for c in concepts}
    ready = [c for c in concepts if c["status"] == "new"
             and all(p in started or p not in known for p in c["prereqs"])]
    if not ready:
        return None
    ready.sort(key=lambda c: (c["position"] - 2.0 * float(c["interest"] or 0.5)))
    return ready[0]


def plan_today(storage, settings, topic_id: int | None = None, ts: float | None = None) -> Plan:
    ts = ts or _now()
    end = day_start(ts) + DAY - 1
    plan = Plan(topic_id=topic_id)
    budget = float(settings.get("learn.session_minutes", 25))
    tired = metrics.fatigue_minutes(storage, ts - 60 * DAY)
    if tired and tired < budget:
        budget = float(tired)
        plan.note = f"Сессия до {tired} мин: дальше у вас обычно падает точность."
    if metrics.tough_day(storage, ts):
        budget = max(5.0, budget / 2)
        plan.kind = "short"
        plan.note = "Сегодня можно коротко — и это нормально."
    elif metrics.missed_yesterday(storage, ts):
        budget = RETURN_MINUTES
        plan.kind = "return"
        plan.note = "Вчера был пропуск. Пять минут, чтобы вернуться, — больше и не нужно."
    plan.budget = budget
    plan.review_min = review_minutes(storage)
    due = storage.due_items(end, topic_id)
    plan.due_total = len(due)
    from . import fsrs
    from .engine import memory_factor
    k = memory_factor(storage)
    due.sort(key=lambda i: (fsrs.current_r(i, ts, k) or 0.0))     # сначала то, что вот-вот забудется
    spent = 0.0
    for item in due:
        if spent + plan.review_min > budget and plan.reviews:
            break
        plan.reviews.append(item)
        spent += plan.review_min
    if topic_id is not None and plan.kind != "return" and int(settings.get("learn.new_per_session", 1)) > 0:
        c_min = concept_minutes(storage)
        if spent + c_min <= budget + 5 or not plan.reviews:
            plan.concept = next_concept(storage, topic_id)
            if plan.concept:
                spent += c_min
    plan.minutes = spent
    cp = storage.checkpoints(topic_id, 1) if topic_id is not None else []
    last = cp[0] if cp else storage.last_checkpoint()
    plan.open_loop = (last or {}).get("open_loop", "")
    if not plan.reviews and not plan.concept:
        plan.kind = "empty" if plan.kind == "normal" else plan.kind
    return plan


def next_due_text(storage, ts: float | None = None) -> str:
    ts = ts or _now()
    row = storage.one("SELECT min(due) AS d, count(*) AS n FROM items WHERE due IS NOT NULL AND due>? "
                      "AND suspended=0", (ts,))
    if not row or not row["d"]:
        return ""
    from ..util import when_text
    n = storage.one("SELECT count(*) AS n FROM items WHERE due<=? AND due>? AND suspended=0",
                    (day_start(row["d"]) + DAY, ts))["n"]
    return f"Следующие повторения — {when_text(row['d'], ts)} ({n})"
