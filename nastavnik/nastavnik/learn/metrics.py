"""Метрики: время, серия дней, усталость, лучшие часы и три метрики мотивации.

Три метрики мотивации считаются отдельно, потому что тяга может расти без удовольствия и пользы
(так работают ленты и игровые автоматы):
- тяга — начали ли сами (без напоминания) и как быстро начали после напоминания;
- удовольствие — оценка сессии 1–5;
- польза — точность на отложенных тестах.
Правило тревоги: две недели тяга растёт, а удовольствие и польза — нет.
"""
from __future__ import annotations

import statistics
from collections import Counter

from ..util import DAY, day_key, day_start, week_start

STUDY_KINDS = ("learn", "review")
MIN_STUDY_MS = 2 * 60 * 1000          # меньше двух минут — не считается днём учёбы
TRIGGER_WINDOW_S = 3 * 3600           # сессия в течение 3 ч после напоминания — «по напоминанию»


def study_sessions(storage, since: float = 0.0) -> list[dict]:
    return [s for s in storage.sessions(since=since) if s["kind"] in STUDY_KINDS]


def week_minutes(storage, now: float) -> float:
    return sum(s["active_ms"] for s in study_sessions(storage, week_start(now))) / 60000


def studied_days(storage, since: float = 0.0) -> set[str]:
    per_day: Counter = Counter()
    for s in study_sessions(storage, since):
        per_day[day_key(s["started"])] += int(s["active_ms"] or 0)
    return {d for d, ms in per_day.items() if ms >= MIN_STUDY_MS}


def streak(storage, now: float, freezes_per_week: int = 1) -> dict:
    """Серия дней подряд с учёбой. Пропуск закрывается «заморозкой» (не больше N в неделю).

    Сегодняшний день ещё не прерывает серию, пока он не кончился."""
    days = studied_days(storage, now - 400 * DAY)
    today = day_key(now)
    count = 0
    used: Counter = Counter()             # неделя → использовано заморозок
    frozen: list[str] = []
    cursor = day_start(now)
    if today in days:
        count = 1
    cursor -= DAY
    while True:
        key = day_key(cursor + 3600)
        if key in days:
            count += 1
        else:
            week = day_key(week_start(cursor + 3600))
            if used[week] < freezes_per_week and any(
                    day_key(cursor - n * DAY + 3600) in days for n in range(1, 3)):
                used[week] += 1
                frozen.append(key)
            else:
                break
        cursor -= DAY
        if count > 3650:
            break
    return {"days": count, "studied_today": today in days, "frozen": frozen}


def missed_yesterday(storage, now: float) -> bool:
    """Вчера не занимались, а раньше занимались — пора короткой сессии-возврата."""
    days = studied_days(storage, now - 30 * DAY)
    yesterday = day_key(day_start(now) - DAY + 3600)
    today = day_key(now)
    return bool(days) and yesterday not in days and today not in days


def fatigue_minutes(storage, since: float = 0.0, bucket_min: int = 5, min_n: int = 6) -> int | None:
    """Через сколько минут сессии точность обычно падает ниже 70 % — дальше сессия не окупается."""
    sessions = {s["id"]: s for s in storage.sessions(kind="learn", since=since, finished=True)}
    buckets: dict[int, list[int]] = {}
    for a in storage.attempts(since=since):
        s = sessions.get(a["session_id"])
        if not s or a["phase"] not in ("practice", "recall", "pretest"):
            continue
        b = int((a["ts"] - s["started"]) / 60 // bucket_min)
        buckets.setdefault(b, []).append(int(a["correct"]))
    for b in sorted(buckets):
        vals = buckets[b]
        if b >= 1 and len(vals) >= min_n and sum(vals) / len(vals) < 0.7:
            return b * bucket_min
    return None


def best_hours(storage, since: float = 0.0) -> list[int]:
    """Часы, в которые вы чаще всего садитесь учиться сами."""
    import time
    hours = Counter(time.localtime(s["started"]).tm_hour for s in study_sessions(storage, since)
                    if s["self_started"] and s["active_ms"] >= MIN_STUDY_MS)
    return [h for h, _ in hours.most_common(2)]


def trigger_for(storage, started: float) -> float | None:
    """Напоминание, после которого началась сессия (если было в последние 3 часа)."""
    rem = [e for e in storage.events("reminder", started - TRIGGER_WINDOW_S) if e["ts"] <= started]
    if not rem:
        return None
    ts = rem[-1]["ts"]
    before = [s for s in study_sessions(storage, ts) if s["started"] < started]
    return None if before else ts


def _window(storage, start: float, end: float) -> dict:
    sess = [s for s in study_sessions(storage, start) if s["started"] < end and s["active_ms"] >= MIN_STUDY_MS]
    self_share = sum(1 for s in sess if s["self_started"]) / len(sess) if sess else None
    lat = [(s["started"] - s["trigger_ts"]) / 60 for s in sess if s["trigger_ts"]]
    likes = [s["liking"] for s in sess if s["liking"]]
    delayed = [a["correct"] for a in storage.attempts(since=start) if a["ts"] < end and a["phase"] == "delayed"]
    return {
        "sessions": len(sess),
        "pull": self_share,                                        # тяга
        "start_minutes": statistics.median(lat) if lat else None,
        "liking": sum(likes) / len(likes) if likes else None,      # удовольствие
        "use": sum(delayed) / len(delayed) if delayed else None,   # польза
        "delayed_n": len(delayed),
    }


def motivation(storage, now: float, days: int = 14) -> dict:
    cur = _window(storage, now - days * DAY, now + 1)
    prev = _window(storage, now - 2 * days * DAY, now - days * DAY)
    alarm = False
    enough = cur["sessions"] >= 4 and prev["sessions"] >= 4
    if enough and cur["pull"] is not None and prev["pull"] is not None:
        pull_up = cur["pull"] - prev["pull"] >= 0.15 or (
            cur["start_minutes"] is not None and prev["start_minutes"] is not None
            and cur["start_minutes"] < prev["start_minutes"] * 0.7)
        like_flat = cur["liking"] is None or prev["liking"] is None or cur["liking"] - prev["liking"] < 0.2
        use_flat = cur["use"] is None or prev["use"] is None or cur["use"] - prev["use"] < 0.03
        alarm = bool(pull_up and like_flat and use_flat)
    return {"current": cur, "previous": prev, "alarm": alarm, "enough": enough}


def tough_day(storage, now: float, hours: float = 12) -> bool:
    """Единственное, что «Разговор» передаёт учёбе: был ли недавно тяжёлый момент (самочувствие ≤ 3).

    Содержание разговоров учёба не видит никогда."""
    for s in storage.sessions(kind="talk", since=now - hours * 3600):
        if s["mood_before"] is not None and s["mood_before"] <= 3:
            return True
    return False


def talk_stats(storage) -> dict:
    """Что помогает в разговорах: изменение самочувствия по режимам и частые темы."""
    by_mode: dict[str, list[int]] = {}
    for s in storage.sessions(kind="talk", finished=True):
        if s["mood_before"] is not None and s["mood_after"] is not None:
            by_mode.setdefault(s["mode"] or "listen", []).append(int(s["mood_after"]) - int(s["mood_before"]))
    modes = {m: {"delta": sum(v) / len(v), "n": len(v)} for m, v in by_mode.items()}
    themes: Counter = Counter()
    techniques: dict[str, int] = Counter()
    for note in storage.talk_notes(200):
        themes.update(note["themes"])
        if note["technique"]:
            techniques[note["technique"]] += 1
    return {"modes": modes, "themes": themes.most_common(8), "techniques": techniques.most_common(5)}
