"""Подбор подачи под вас: выборка Томпсона (бандит) по каждому эксперименту.

Варианты не «нравятся/не нравятся», а проверяются результатом:
- порядок, подача и закрепление — отложенным тестом (первое повторение понятия через 1,5+ дня);
- крючок и подарок — оценкой сессии 4–5 из 5.
Часть сессий отдаётся «разведке» (вариант, который пробовали реже всего), чтобы бандит не
застрял на случайном раннем победителе.
"""
from __future__ import annotations

import random

EXPERIMENTS: dict[str, dict] = {
    "order": {
        "title": "Порядок",
        "reward": "отложенный тест",
        "arms": {"task_first": "Сначала задача, потом объяснение",
                 "theory_first": "Сначала объяснение, потом задача"},
    },
    "present": {
        "title": "Подача",
        "reward": "отложенный тест",
        "arms": {"schema": "Схема и короткий текст",
                 "interest_example": "Пример из ваших интересов",
                 "analogy": "Аналогия из жизни"},
    },
    "recall": {
        "title": "Закрепление",
        "reward": "отложенный тест",
        "arms": {"schema_recall": "Схема по памяти",
                 "own_words": "Объяснить своими словами",
                 "transfer": "Задача на перенос"},
    },
    "hook": {
        "title": "Крючок в начале",
        "reward": "оценка сессии",
        "arms": {"riddle": "Загадка", "paradox": "Парадокс", "life_case": "Случай из жизни",
                 "challenge": "Мини-вызов"},
    },
    "gift": {
        "title": "Подарок в конце",
        "reward": "оценка сессии",
        "arms": {"project_link": "Связь с вашим проектом", "now_you_can": "«Теперь ты можешь…»",
                 "secret": "Секрет темы", "application": "Красивое применение"},
    },
}
CONTENT_EXPERIMENTS = ("order", "present", "recall")     # награда — отложенный тест, контекст — тип понятия
SESSION_EXPERIMENTS = ("hook", "gift")                   # награда — оценка сессии


def arm_label(experiment: str, arm: str) -> str:
    return EXPERIMENTS.get(experiment, {}).get("arms", {}).get(arm, arm)


def choose(storage, experiment: str, context: str, explore_share: float = 0.15,
           rng: random.Random | None = None) -> str:
    rng = rng or random
    arms = list(EXPERIMENTS[experiment]["arms"])
    stats = storage.arm_stats(experiment, context)
    if rng.random() < explore_share:
        least = min(int(stats.get(a, {}).get("n", 0)) for a in arms)
        return rng.choice([a for a in arms if int(stats.get(a, {}).get("n", 0)) == least])
    best, best_v = arms[0], -1.0
    for a in arms:
        st = stats.get(a) or {}
        v = rng.betavariate(float(st.get("alpha", 1.0)), float(st.get("beta", 1.0)))
        if v > best_v:
            best, best_v = a, v
    return best


def choose_session(storage, concept_kind: str = "concept", explore_share: float = 0.15,
                   rng: random.Random | None = None) -> dict[str, str]:
    """Все форматы на одну сессию."""
    out = {e: choose(storage, e, concept_kind, explore_share, rng) for e in CONTENT_EXPERIMENTS}
    out.update({e: choose(storage, e, "all", explore_share, rng) for e in SESSION_EXPERIMENTS})
    out["kind"] = concept_kind
    return out


def reward_content(storage, arms: dict, correct: bool, weight: float = 1.0) -> None:
    """Отложенный тест понятия, введённого в сессии с этими форматами.

    weight — доля понятия: у понятия с тремя карточками каждая весит 1/3, чтобы понятия с
    большим числом карточек не перевешивали остальные."""
    ctx = arms.get("kind", "concept")
    for e in CONTENT_EXPERIMENTS:
        if arms.get(e) in EXPERIMENTS[e]["arms"]:
            storage.update_arm(e, ctx, arms[e], 1.0 if correct else 0.0, weight)


def reward_session(storage, arms: dict, liking: int) -> None:
    good = 1.0 if liking >= 4 else 0.0
    for e in SESSION_EXPERIMENTS:
        if arms.get(e) in EXPERIMENTS[e]["arms"]:
            storage.update_arm(e, "all", arms[e], good)


def table(storage) -> list[dict]:
    """Что работает на вас: по каждому эксперименту и типу понятия — доля успеха и число проверок."""
    rows = []
    contexts = {"concept": "понятия", "procedure": "процедуры", "all": ""}
    for e, spec in EXPERIMENTS.items():
        ctx_list = ["all"] if e in SESSION_EXPERIMENTS else ["concept", "procedure"]
        for ctx in ctx_list:
            stats = storage.arm_stats(e, ctx)
            arms = []
            for a, label in spec["arms"].items():
                st = stats.get(a) or {}
                alpha, beta, n = float(st.get("alpha", 1)), float(st.get("beta", 1)), int(st.get("n", 0))
                arms.append({"arm": a, "label": label, "mean": alpha / (alpha + beta), "n": n})
            total = sum(x["n"] for x in arms)
            if not total and ctx == "procedure":
                continue
            rows.append({"experiment": e, "title": spec["title"], "context": contexts[ctx], "reward": spec["reward"],
                         "arms": arms, "total": total})
    return rows
