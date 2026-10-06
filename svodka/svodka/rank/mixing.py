"""Лента сама решает, насколько доверять каждой части оценки — именно для вас.

Оценка статьи — смесь частей: ваш вкус (персональная модель), оценка Claude «насколько
совпадает с профилем», важность события в мире, свежесть. Стартовые веса заданы вручную,
но у разных людей полезность частей разная: кому-то важнее «главное в мире», кому-то — только
своё. Большие системы подбирают такие веса по данным; делаем так же, но осторожно:

- при каждом показе запоминается, как ранжирование оценило статью (до ваших действий —
  без подглядывания в ответ);
- раз в построение ленты ищем веса, при которых оценка лучше предсказывает итоговую ценность
  (взвешенная гребневая регрессия, отрицательные веса не допускаются);
- выученные веса смешиваются со стартовыми пропорционально объёму данных.
"""
from __future__ import annotations

import time

from ..util import jload
from .signals import exam_prob

PARTS = ("personal", "fit", "importance", "fresh")
MIN_OBS = 40           # меньше показов — веса не трогаем
TRUST_AT = 150         # при стольких наблюдениях выученные веса весят половину
RIDGE = 2.0
FLOOR = 0.03


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Решение небольшой линейной системы методом Гаусса."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def learn_mix(storage, signals: dict, default: dict, now: float | None = None,
              days: int = 30) -> tuple[dict, dict]:
    now = now if now is not None else time.time()
    rows = storage.query("SELECT article_id, position, parts FROM impressions WHERE parts != '' AND ts >= ? "
                         "ORDER BY ts, id", (now - days * 86400,))
    first: dict[int, tuple[dict, int]] = {}
    for r in rows:
        if r["article_id"] not in first:
            first[r["article_id"]] = (jload(r["parts"], {}), int(r["position"]))
    xs, ys, ws = [], [], []
    for aid, (parts, pos) in first.items():
        sig = signals.get(aid)
        if sig is None:
            continue
        engaged = any(c.label != "пропуск" for c in sig.contribs)
        xs.append([float(parts.get(k, 0.5)) for k in PARTS])
        ys.append(sig.value)
        ws.append(1.0 if engaged else exam_prob(pos))
    n = len(xs)
    report = {"n": n}
    if n < MIN_OBS:
        return dict(default), report
    sw = sum(ws)
    mx = [sum(w * x[k] for w, x in zip(ws, xs)) / sw for k in range(len(PARTS))]
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    k = len(PARTS)
    a = [[RIDGE * (i == j) for j in range(k)] for i in range(k)]
    b = [0.0] * k
    for w, x, y in zip(ws, xs, ys):
        dx = [x[i] - mx[i] for i in range(k)]
        dy = y - my
        for i in range(k):
            b[i] += w * dx[i] * dy
            for j in range(k):
                a[i][j] += w * dx[i] * dx[j]
    beta = _solve(a, b)
    if beta is None:
        return dict(default), report
    pos_beta = [max(0.0, v) for v in beta]
    total = sum(pos_beta)
    if total <= 0:
        return dict(default), report
    learned = {p: pos_beta[i] / total for i, p in enumerate(PARTS)}
    alpha = n / (n + TRUST_AT)
    mix = {p: (1 - alpha) * default[p] + alpha * learned[p] for p in PARTS}
    mix = {p: max(FLOOR, v) for p, v in mix.items()}
    s = sum(mix.values())
    mix = {p: v / s for p, v in mix.items()}
    report.update({"learned": {p: round(v, 3) for p, v in learned.items()}, "alpha": round(alpha, 2),
                   "raw": [round(v, 3) for v in beta]})
    return mix, report
