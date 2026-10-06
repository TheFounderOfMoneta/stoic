"""Подстройка весов по опросам и честная проверка качества.

YouTube учится предсказывать ответы на опросы «было ли это ценно» по поведению.
У нас данных мало, поэтому подход осторожный и объяснимый:
- для каждого неявного сигнала (раскрыл, пробежал, дочитал, вернулся, короткий клик)
  смотрим, как часто статьи с ним получали ★4–5, по сравнению с общей долей;
- оценку сглаживаем к общей доле (мало примеров — почти не двигаем);
- вес сдвигается в ту же сторону, но не больше чем в 3 раза от стартового.

Новые веса включаются, только если на прошлых днях они ставят в топ-5 не меньше ценного
(офлайн-проверка на журнале, как предложили в Yahoo для новостей).
"""
from __future__ import annotations

import math
import time

from ..util import day_key
from .model import InterestModel
from .signals import DEFAULT_WEIGHTS, IMPLICIT, collect_signals, merge_weights

MIN_SURVEYS = 15
SMOOTH = 5.0


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def propose_weights(storage, current: dict | None = None) -> tuple[dict, dict] | None:
    """Новые веса неявных сигналов по опросам. None — если данных пока мало."""
    current = merge_weights(current or storage.active_weights())
    sigs = collect_signals(storage, weights=current)
    rated = [s for s in sigs.values() if s.stars is not None]
    if len(rated) < MIN_SURVEYS:
        return None
    good = [s for s in rated if s.stars >= 4]
    if not good or len(good) == len(rated):
        return None
    base = len(good) / len(rated)
    flags = {
        "expand": lambda s: s.implicit.get("expand"),
        "skim": lambda s: s.implicit.get("skim"),
        "read": lambda s: s.implicit.get("read_score", 0) >= 0.6,
        "revisit": lambda s: s.implicit.get("revisit"),
        "short_click_source": lambda s: s.implicit.get("short_click"),
        "impression_skip": lambda s: s.implicit.get("skipped"),
    }
    new = dict(current)
    report = {}
    for key in IMPLICIT:
        test = flags[key]
        with_sig = [s for s in rated if test(s)]
        n = len(with_sig)
        k = sum(1 for s in with_sig if s.stars >= 4)
        rate = (k + SMOOTH * base) / (n + SMOOTH)
        effect = _logit(rate) - _logit(base)
        default = DEFAULT_WEIGHTS[key]
        sign = 1.0 if default >= 0 else -1.0
        factor = min(3.0, max(1 / 3, math.exp(sign * effect)))
        new[key] = round(default * factor, 4)
        if key == "short_click_source":
            new["short_click_content"] = round(DEFAULT_WEIGHTS["short_click_content"] * factor, 4)
        report[key] = {"n": n, "good": k, "rate": round(rate, 3), "base": round(base, 3),
                       "factor": round(factor, 2)}
    return new, report


def replay_precision(storage, weights: dict | None, days: int = 14, now: float | None = None,
                     k: int = 5) -> float | None:
    """Доля ценного в топ-k на прошлых днях: модель строится только из того, что было до дня."""
    now = now if now is not None else time.time()
    label_sigs = collect_signals(storage)            # «правда» — по стандартным весам, одинаково для всех
    by_day: dict[str, set[int]] = {}
    for r in storage.query("SELECT DISTINCT article_id, ts FROM impressions WHERE ts >= ?",
                           (now - days * 86400,)):
        by_day.setdefault(day_key(r["ts"]), set()).add(r["article_id"])
    scores = []
    for day, ids in sorted(by_day.items()):
        if len(ids) < k:
            continue
        day_start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        model = InterestModel.build(storage, now=day_start, until=day_start, weights=weights)
        ranked = []
        for aid in ids:
            a = storage.article(aid)
            if not a:
                continue
            ranked.append((model.personal(a).score + 0.3 * float(a.get("fit") or 0.5), aid))
        ranked.sort(reverse=True)
        top = [aid for _s, aid in ranked[:k]]
        good = sum(1 for aid in top if label_sigs.get(aid) and label_sigs[aid].valuable)
        scores.append(good / k)
    if not scores:
        return None
    return sum(scores) / len(scores)


def maybe_recalibrate(storage) -> dict | None:
    """Раз в неделю: предложить веса, проверить на прошлом, включить если не хуже."""
    last = float(storage.meta_get("calibrated_at", "0") or 0)
    if time.time() - last < 6 * 86400:
        return None
    storage.meta_set("calibrated_at", str(time.time()))
    proposal = propose_weights(storage)
    if not proposal:
        return None
    new, report = proposal
    old = merge_weights(storage.active_weights())
    p_old = replay_precision(storage, old)
    p_new = replay_precision(storage, new)
    accepted = p_old is None or p_new is None or p_new >= p_old - 0.01
    if accepted:
        storage.save_weights(new, note=f"по опросам; точность топ-5 {p_old} → {p_new}")
    return {"accepted": accepted, "old": p_old, "new": p_new, "report": report}


def quality(storage, days: int = 7, now: float | None = None) -> dict:
    """Метрики для экрана «Качество ленты» (последние days дней)."""
    now = now if now is not None else time.time()
    since = now - days * 86400
    sigs = collect_signals(storage)
    shown = storage.query("SELECT article_id, MIN(position) AS pos FROM impressions WHERE ts >= ? "
                          "AND visible_ms >= 1000 GROUP BY article_id", (since,))
    top5 = [r["article_id"] for r in shown if r["pos"] < 5]
    all_shown = [r["article_id"] for r in shown]

    def share(ids, pred):
        ids = [i for i in ids if i in sigs]
        return round(sum(1 for i in ids if pred(sigs[i])) / len(ids), 3) if ids else None

    reads = storage.query("SELECT article_id, SUM(active_ms) AS ms FROM reads WHERE ts >= ? GROUP BY article_id",
                          (since,))
    total_ms = sum(r["ms"] for r in reads) or 0
    valuable_ms = sum(r["ms"] for r in reads if r["article_id"] in sigs and sigs[r["article_id"]].valuable)
    explore_ids = [r["id"] for r in storage.query("SELECT id FROM articles WHERE bucket='explore' AND collected_at >= ?",
                                                    (since,))]
    explore_shown = [i for i in explore_ids if i in sigs and sigs[i].shown]
    topics = {r["topic"] for r in storage.query(
        "SELECT DISTINCT a.topic FROM reads r JOIN articles a ON a.id = r.article_id WHERE r.ts >= ? "
        "AND r.active_ms >= 20000", (since,))}
    return {
        "precision_top5": share(top5, lambda s: s.valuable),
        "reject_rate": share(all_shown, lambda s: s.rejected),
        "valuable_time": round(valuable_ms / total_ms, 3) if total_ms else None,
        "read_minutes": round(total_ms / 60000, 1),
        "topics_read": len(topics),
        "explore_success": share(explore_shown, lambda s: s.valuable),
        "shown": len(all_shown),
    }
