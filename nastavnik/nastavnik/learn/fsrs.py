"""Интервальные повторения: FSRS-5 (формулы и параметры по умолчанию открытого FSRS) + личная калибровка.

Модель памяти — три числа на карточку:
- стабильность S — через сколько дней вероятность вспомнить упадёт до 90 %;
- трудность D — от 1 до 10;
- вероятность вспомнить R — считается из S и прошедших дней.

Личная подстройка без тяжёлых зависимостей: когда набирается достаточно повторений с
перерывом от суток, подбирается один множитель памяти k (S_личная = k·S) — тот, при котором
прогнозы лучше всего совпадают с тем, что вы на самом деле вспомнили (минимум log loss).
k > 1 — вы помните дольше среднего, интервалы растягиваются; k < 1 — наоборот.
"""
from __future__ import annotations

import math

# Параметры FSRS-5 по умолчанию (обучены на сотнях миллионов повторений пользователей Anki).
W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192, 1.01925,
     1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621]
DECAY = -0.5
FACTOR = 19 / 81            # при t = S вероятность вспомнить ровно 0,9
DAY = 86400.0
RELEARN_S = 600             # «Снова» — показать ещё раз через 10 минут
MAX_INTERVAL_DAYS = 3 * 365
MIN_CALIBRATION = 100       # столько повторений с перерывом от суток нужно для личного множителя

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
GRADE_LABELS = {AGAIN: "Снова", HARD: "Трудно", GOOD: "Хорошо", EASY: "Легко"}


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def retrievability(elapsed_days: float, stability: float) -> float:
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def interval_days(stability: float, desired: float = 0.9) -> float:
    return stability / FACTOR * (desired ** (1 / DECAY) - 1)


def init_stability(grade: int) -> float:
    return W[grade - 1]


def init_difficulty(grade: int) -> float:
    return clamp(W[4] - math.exp(W[5] * (grade - 1)) + 1, 1, 10)


def next_difficulty(d: float, grade: int) -> float:
    delta = -W[6] * (grade - 3)
    d2 = d + delta * (10 - d) / 9            # чем выше трудность, тем медленнее она растёт
    return clamp(W[7] * init_difficulty(EASY) + (1 - W[7]) * d2, 1, 10)


def recall_stability(d: float, s: float, r: float, grade: int) -> float:
    hard = W[15] if grade == HARD else 1.0
    easy = W[16] if grade == EASY else 1.0
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp((1 - r) * W[10]) - 1) * hard * easy)


def forget_stability(d: float, s: float, r: float) -> float:
    new = W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])
    return min(new, s)


def short_term_stability(s: float, grade: int) -> float:
    return s * math.exp(W[17] * (grade - 3 + W[18]))


def review(card: dict, grade: int, now: float, k: float = 1.0, desired: float = 0.9) -> dict:
    """Новое состояние карточки после ответа с оценкой 1–4.

    card: {state, stability, difficulty, last_review, reps, lapses}. Возвращает поля для записи
    в базу и служебное: elapsed_days, retrievability (прогноз до ответа, без личного множителя).
    """
    grade = int(clamp(grade, AGAIN, EASY))
    reps = int(card.get("reps") or 0)
    s = float(card.get("stability") or 0)
    d = float(card.get("difficulty") or 0)
    last = card.get("last_review")
    elapsed = (now - float(last)) / DAY if last else 0.0
    r_raw = retrievability(elapsed, s) if reps and s > 0 else None
    lapses = int(card.get("lapses") or 0)
    if reps and s > 0 and not 1 <= d <= 10:          # испорченная или неполная запись — чиним, а не падаем
        d = clamp(d, 1, 10) if d > 0 else init_difficulty(GOOD)
    if not reps or s <= 0:
        s, d = init_stability(grade), init_difficulty(grade)
    elif elapsed < 1.0:
        s, d = short_term_stability(s, grade), next_difficulty(d, grade)
    elif grade == AGAIN:
        s, d = forget_stability(d, s, r_raw or 0.0), next_difficulty(d, grade)
        lapses += 1
    else:
        s, d = recall_stability(d, s, r_raw or 0.0, grade), next_difficulty(d, grade)
    s = clamp(s, 0.01, 36500)
    if grade == AGAIN:
        due = now + RELEARN_S
        state = "relearning"
    else:
        days = clamp(round(interval_days(s * k, desired)), 1, MAX_INTERVAL_DAYS)
        due = now + days * DAY
        state = "review"
    return {"state": state, "stability": s, "difficulty": d, "due": due, "last_review": now, "reps": reps + 1,
            "lapses": lapses, "elapsed_days": elapsed if reps else None, "retrievability": r_raw}


def preview(card: dict, now: float, k: float = 1.0, desired: float = 0.9) -> dict[int, str]:
    """Подписи под кнопками оценок: через сколько будет следующее повторение."""
    out = {}
    for g in (AGAIN, HARD, GOOD, EASY):
        due = review(card, g, now, k, desired)["due"]
        out[g] = interval_text(due - now)
    return out


def interval_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} мин"
    if seconds < DAY:
        return f"{round(seconds / 3600)} ч"
    days = seconds / DAY
    if days < 31:
        return f"{round(days)} дн"
    if days < 365:
        return f"{round(days / 30)} мес"
    return f"{days / 365:.1f} г"


def current_r(card: dict, now: float, k: float = 1.0) -> float | None:
    if not card.get("reps") or not card.get("last_review") or not card.get("stability"):
        return None
    return retrievability((now - float(card["last_review"])) / DAY, float(card["stability"]) * k)


def grade_from_accuracy(acc: float | None) -> int:
    """Начальная оценка для карточек из сессии: как понятие получилось на практике."""
    if acc is None:
        return GOOD
    if acc >= 0.8:
        return GOOD
    if acc >= 0.5:
        return HARD
    return AGAIN


# ---------------------------------------------------------------- личная калибровка
def _stability_from(r_raw: float, elapsed: float) -> float | None:
    if not 0 < r_raw < 1 or elapsed <= 0:
        return None
    denom = r_raw ** (1 / DECAY) - 1
    return FACTOR * elapsed / denom if denom > 0 else None


def fit_memory_factor(samples: list[tuple[float, float, int]]) -> tuple[float, int]:
    """samples: [(retrievability без множителя, прошло дней, вспомнил 0/1)].

    Возвращает (k, сколько примеров учтено). Мало данных — k = 1 (параметры по умолчанию)."""
    data = []
    for r_raw, elapsed, ok in samples:
        if r_raw is None or elapsed is None or elapsed < 1.0:
            continue
        s = _stability_from(float(r_raw), float(elapsed))
        if s:
            data.append((s, float(elapsed), 1 if ok else 0))
    if len(data) < MIN_CALIBRATION:
        return 1.0, len(data)
    best_k, best_loss = 1.0, float("inf")
    k = 0.4
    while k <= 2.5001:
        loss = 0.0
        for s, t, ok in data:
            p = clamp(retrievability(t, s * k), 1e-4, 1 - 1e-4)
            loss -= math.log(p) if ok else math.log(1 - p)
        # мягкое притяжение к 1: без явных данных не уходим далеко от средних параметров
        loss += 2.0 * math.log(k) ** 2
        if loss < best_loss:
            best_k, best_loss = k, loss
        k = round(k + 0.05, 2)
    return best_k, len(data)
