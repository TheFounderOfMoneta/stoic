"""Связка базы, FSRS и бандитов: запись повторений, карточки из сессий, освоенность понятий.

Здесь одно правило на всё приложение: окно, MCP-сервер (через который пишет Claude) и тесты
меняют состояние учёбы только через эти функции.
"""
from __future__ import annotations

from ..util import DAY, now as _now
from . import bandit, fsrs

DELAYED_AFTER_DAYS = 1.5          # первое повторение понятия не раньше — это и есть отложенный тест
MASTERED_STABILITY = 21.0         # карточки держатся в памяти от трёх недель
FACTOR_KEY = "memory_factor"
FACTOR_N_KEY = "memory_factor_n"


def memory_factor(storage) -> float:
    try:
        return float(storage.meta_get(FACTOR_KEY, "1.0") or 1.0)
    except ValueError:
        return 1.0


def recalibrate(storage) -> tuple[float, int]:
    """Пересчитать личный множитель памяти по всем повторениям с перерывом от суток."""
    rows = storage.query("SELECT retrievability, elapsed_days, correct FROM attempts "
                         "WHERE phase IN ('review', 'delayed', 'ahead') AND elapsed_days IS NOT NULL")
    k, n = fsrs.fit_memory_factor([(r["retrievability"], r["elapsed_days"], r["correct"]) for r in rows])
    storage.meta_set(FACTOR_KEY, f"{k:.2f}")
    storage.meta_set(FACTOR_N_KEY, str(n))
    return k, n


def is_delayed_test(storage, item: dict, ts: float) -> bool:
    """Первое повторение карточки понятия спустя 1,5+ дня после того, как понятие ввели.
    Если понятие уже повторяли сами раньше срока, проверка нечистая: вспоминать помогло
    повторение, а не формат подачи, — такое не считаем отложенным тестом."""
    if not item.get("concept_id"):
        return False
    prior = storage.one("SELECT count(*) AS n FROM attempts WHERE item_id=? AND phase IN ('review', 'delayed')",
                        (item["id"],))
    if prior and prior["n"]:
        return False
    early = storage.one("SELECT count(*) AS n FROM attempts WHERE concept_id=? AND phase='ahead' AND ts<?",
                        (item["concept_id"], ts))
    if early and early["n"]:
        return False
    concept = storage.concept(item["concept_id"])
    intro = (concept or {}).get("introduced_at") or item.get("created_at")
    return bool(intro) and ts - float(intro) >= DELAYED_AFTER_DAYS * DAY


def record_review(storage, settings, item_id: int, grade: int, latency_ms: int = 0, confidence: int | None = None,
                  session_id: int | None = None, ts: float | None = None, ahead: bool = False) -> dict:
    """Ответ на карточку в Повторении. Возвращает новое состояние карточки и фазу.

    ahead — повторяете сами, раньше срока. FSRS учитывает и такое повторение (вспоминать сразу легче,
    поэтому срок сдвигается меньше). Если с введения понятия прошло 1,5+ дня, это всё равно честный
    отложенный тест; раньше — фаза «ahead», и проверка форматов по этому понятию пропускается."""
    ts = ts or _now()
    item = storage.item(item_id)
    if not item:
        return {}
    k = memory_factor(storage)
    desired = float(settings.get("learn.desired_retention", 0.9)) if settings else 0.9
    delayed = is_delayed_test(storage, item, ts)
    new = fsrs.review(item, grade, ts, k=k, desired=desired)
    storage.update_item(item_id, **{f: new[f] for f in ("state", "stability", "difficulty", "due", "last_review",
                                                         "reps", "lapses")})
    phase = "delayed" if delayed else ("ahead" if ahead else "review")
    storage.log_attempt(phase, grade >= fsrs.HARD, grade=grade, session_id=session_id, topic_id=item["topic_id"],
                        concept_id=item["concept_id"], item_id=item_id, latency_ms=latency_ms,
                        confidence=confidence, elapsed_days=new["elapsed_days"],
                        retrievability=new["retrievability"], ts=ts)
    if delayed:
        concept = storage.concept(item["concept_id"])
        intro = storage.session(concept["intro_session"]) if concept and concept.get("intro_session") else None
        if intro and intro.get("arms"):
            n_items = max(1, len([i for i in storage.items(concept_id=item["concept_id"]) if not i["suspended"]]))
            bandit.reward_content(storage, intro["arms"], grade >= fsrs.HARD, 1.0 / n_items)
    if item["concept_id"]:
        refresh_concept(storage, item["concept_id"])
    n_cal = int(storage.meta_get(FACTOR_N_KEY, "0") or 0)
    total = storage.one("SELECT count(*) AS n FROM attempts WHERE phase IN ('review','delayed','ahead') "
                        "AND elapsed_days IS NOT NULL")["n"]
    if total >= fsrs.MIN_CALIBRATION and total - n_cal >= 25:
        recalibrate(storage)
    new["phase"] = phase
    return new


def concept_accuracy(storage, concept_id: int, session_id: int | None = None,
                     phases: tuple[str, ...] = ("practice", "recall")) -> float | None:
    """Доля верных ответов по понятию. Крючок (pretest) не считается: это вопрос до объяснения,
    ошибиться в нём нормально — ради этого он и задаётся."""
    sql = f"SELECT correct FROM attempts WHERE concept_id=? AND phase IN ({','.join('?' * len(phases))})"
    params: list = [concept_id, *phases]
    if session_id is not None:
        sql += " AND session_id=?"
        params.append(session_id)
    rows = storage.query(sql, params)
    if not rows:
        return None
    return sum(r["correct"] for r in rows) / len(rows)


def add_session_items(storage, topic_id: int, concept_id: int | None, items: list[dict],
                      session_id: int | None = None, ts: float | None = None) -> list[int]:
    """Карточки, которые Claude сделал в сессии. Стартовое состояние — по тому, как понятие
    получилось на практике: хорошо — первое повторение через ~3 дня, плохо — уже завтра."""
    ts = ts or _now()
    acc = concept_accuracy(storage, concept_id, session_id) if concept_id else None
    grade = fsrs.grade_from_accuracy(acc)
    if acc is None and concept_id and concept_accuracy(storage, concept_id, session_id, ("pretest",)) is not None:
        grade = fsrs.HARD               # был только крючок, без практики — проверим уже завтра
    ids = []
    for it in items:
        prompt = (it.get("prompt") or "").strip()
        if not prompt:
            continue
        item_id = storage.add_item(topic_id, concept_id, prompt, it.get("answer", ""), it.get("kind", "card"),
                                   session_id=session_id)
        st = fsrs.review({}, grade, ts, k=memory_factor(storage))
        if grade == fsrs.AGAIN:
            st["due"] = ts + 0.8 * DAY      # не через 10 минут, а завтра: сессия уже закончилась
        storage.update_item(item_id, state=st["state"], stability=st["stability"], difficulty=st["difficulty"],
                            due=st["due"], last_review=ts, reps=1)
        ids.append(item_id)
    if concept_id:
        refresh_concept(storage, concept_id)
    return ids


def introduce_concept(storage, concept_id: int, session_id: int | None, ts: float | None = None) -> None:
    c = storage.concept(concept_id)
    if not c:
        return
    fields = {"status": "learning" if c["status"] == "new" else c["status"]}
    if not c.get("intro_session"):
        fields.update(intro_session=session_id, introduced_at=ts or _now())
    storage.update_concept(concept_id, **fields)


def refresh_concept(storage, concept_id: int) -> str:
    """new → learning → mastered. Освоено: все карточки держатся ≥ 21 дня и отложенный тест сдан."""
    c = storage.concept(concept_id)
    if not c:
        return ""
    items = [i for i in storage.items(concept_id=concept_id) if not i["suspended"]]
    status = c["status"]
    if items or c.get("introduced_at"):
        status = "learning"
    if items and all(float(i["stability"] or 0) >= MASTERED_STABILITY for i in items):
        last_delayed = storage.one("SELECT correct FROM attempts WHERE concept_id=? AND phase IN ('delayed','review') "
                                   "ORDER BY ts DESC LIMIT 1", (concept_id,))
        if last_delayed and last_delayed["correct"]:
            status = "mastered"
    if status != c["status"]:
        storage.update_concept(concept_id, status=status)
    return status


def topic_progress(storage, topic_id: int, ts: float | None = None) -> dict:
    ts = ts or _now()
    concepts = storage.concepts(topic_id)
    counts = {"new": 0, "learning": 0, "mastered": 0}
    for c in concepts:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    k = memory_factor(storage)
    items = storage.items(topic_id=topic_id)
    rs = [r for r in (fsrs.current_r(i, ts, k) for i in items) if r is not None]
    due = len(storage.due_items(_end_of_day(ts), topic_id))
    ahead = [i["due"] for i in items if i["due"] and i["due"] > _end_of_day(ts) and not i["suspended"]]
    # «пройдено» — разобрали в сессии (засчитывается сразу); «освоено» (mastered) — закреплено:
    # все карточки держатся от трёх недель и проверка через несколько дней сдана
    return {"total": len(concepts), **counts, "studied": counts["learning"] + counts["mastered"],
            "retention": sum(rs) / len(rs) if rs else None, "due": due, "cards": len(rs),
            "next_review": min(ahead) if ahead else None}


def retention_all(storage, ts: float | None = None) -> float | None:
    ts = ts or _now()
    k = memory_factor(storage)
    rs = [r for r in (fsrs.current_r(i, ts, k) for i in storage.items()) if r is not None]
    return sum(rs) / len(rs) if rs else None


def _end_of_day(ts: float) -> float:
    from ..util import day_start
    return day_start(ts) + DAY - 1
