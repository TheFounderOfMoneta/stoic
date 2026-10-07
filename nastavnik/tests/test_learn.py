"""Ядро обучения: память (FSRS и личный множитель), бандиты форматов, план, серия, усталость, мотивация.

Проверки — на симулированном ученике с известными свойствами: система должна их обнаружить.
"""
from __future__ import annotations

import math
import random

import pytest

from nastavnik.config import Settings
from nastavnik.learn import bandit, engine, fsrs, metrics, planner
from nastavnik.storage import Storage
from nastavnik.util import DAY, day_start

T0 = day_start(1_780_000_000) + 10 * 3600      # фиксированное «сейчас» в 14:00


@pytest.fixture()
def env(tmp_path):
    st = Storage(tmp_path / "db.sqlite3")
    settings = Settings(tmp_path / "settings.json")
    yield st, settings
    st.close()


def make_topic(st, n=5, chain=True):
    tid = st.add_topic("Сети", "понимать TCP/IP")
    concepts = []
    for i in range(n):
        concepts.append({"slug": f"c{i}", "title": f"Понятие {i}", "kind": "concept",
                         "prereqs": [f"c{i - 1}"] if chain and i else []})
    st.add_concepts(tid, concepts)
    return tid


# ---------------------------------------------------------------- FSRS
def test_fsrs_basics():
    assert fsrs.retrievability(10, 10) == pytest.approx(0.9, abs=1e-9)
    assert fsrs.interval_days(10, 0.9) == pytest.approx(10, rel=1e-6)
    card = {}
    t = T0
    intervals = []
    for _ in range(5):
        card = fsrs.review(card, fsrs.GOOD, t)
        intervals.append(card["due"] - t)
        t = card["due"]
    assert intervals == sorted(intervals) and intervals[-1] > 20 * DAY
    forgot = fsrs.review(card, fsrs.AGAIN, t)
    assert forgot["stability"] < card["stability"] and forgot["due"] - t <= 600 and forgot["lapses"] == 1
    labels = fsrs.preview(card, t)
    assert labels[fsrs.AGAIN] == "10 мин" and labels[fsrs.EASY].endswith(("дн", "мес", "г"))


def test_personal_memory_factor_is_found():
    """У человека память на 60 % дольше средней: калибровка должна это увидеть."""
    rng = random.Random(1)
    k_true = 1.6
    samples = []
    for _ in range(600):
        s = rng.uniform(1, 40)
        t = rng.uniform(1, 60)
        r_model = fsrs.retrievability(t, s)
        recalled = rng.random() < fsrs.retrievability(t, s * k_true)
        samples.append((r_model, t, int(recalled)))
    k, n = fsrs.fit_memory_factor(samples)
    assert n == 600
    assert abs(k - k_true) <= 0.25
    k_small, n_small = fsrs.fit_memory_factor(samples[:40])
    assert k_small == 1.0 and n_small == 40          # мало данных — параметры по умолчанию


# ---------------------------------------------------------------- бандиты
def test_bandit_finds_what_works_for_this_learner(env):
    """Этому ученику «сначала задача» даёт 80 % на отложенном тесте, «сначала объяснение» — 55 %."""
    st, _ = env
    rng = random.Random(7)
    truth = {"task_first": 0.8, "theory_first": 0.55}
    picks = []
    for _ in range(160):
        arm = bandit.choose(st, "order", "concept", explore_share=0.1, rng=rng)
        picks.append(arm)
        st.update_arm("order", "concept", arm, 1.0 if rng.random() < truth[arm] else 0.0)
    late = picks[-60:]
    assert late.count("task_first") / len(late) >= 0.7
    row = next(r for r in bandit.table(st) if r["experiment"] == "order" and r["context"] == "понятия")
    means = {a["arm"]: a["mean"] for a in row["arms"]}
    assert means["task_first"] > means["theory_first"]
    assert all(a["n"] > 0 for a in row["arms"])       # разведка не дала забыть второй вариант


def test_delayed_test_rewards_formats_of_intro_session(env):
    st, settings = env
    tid = make_topic(st, 2)
    c0 = st.concept_by_slug(tid, "c0")
    arms = {"order": "task_first", "present": "schema", "recall": "own_words", "hook": "riddle",
            "gift": "secret", "kind": "concept"}
    sid = st.start_session("learn", tid, started=T0, arms=arms)
    engine.introduce_concept(st, c0["id"], sid, ts=T0)
    st.log_attempt("practice", True, session_id=sid, topic_id=tid, concept_id=c0["id"], ts=T0 + 60)
    ids = engine.add_session_items(st, tid, c0["id"], [{"prompt": "Что такое пакет?", "answer": "Порция данных"}],
                                   session_id=sid, ts=T0 + 120)
    item = st.item(ids[0])
    assert 2 * DAY <= item["due"] - (T0 + 120) <= 4 * DAY       # хорошо на практике — повтор через ~3 дня
    # повтор в тот же день — ещё не отложенный тест
    assert not engine.is_delayed_test(st, item, T0 + 3600)
    res = engine.record_review(st, settings, ids[0], fsrs.GOOD, latency_ms=4000, ts=T0 + 3 * DAY)
    assert res["phase"] == "delayed"
    assert st.arm_stats("order", "concept")["task_first"]["n"] == 1
    assert st.arm_stats("recall", "concept")["own_words"]["alpha"] == 2.0
    res2 = engine.record_review(st, settings, ids[0], fsrs.GOOD, ts=T0 + 9 * DAY)
    assert res2["phase"] == "review"                        # награда только за первый отложенный тест
    assert st.arm_stats("order", "concept")["task_first"]["n"] == 1


def test_bad_practice_brings_review_tomorrow(env):
    st, _ = env
    tid = make_topic(st, 1)
    c = st.concept_by_slug(tid, "c0")
    sid = st.start_session("learn", tid, started=T0)
    for ok in (0, 0, 1):
        st.log_attempt("practice", bool(ok), session_id=sid, concept_id=c["id"], ts=T0)
    ids = engine.add_session_items(st, tid, c["id"], [{"prompt": "q", "answer": "a"}], session_id=sid, ts=T0)
    assert st.item(ids[0])["due"] - T0 < 1.1 * DAY


def test_wrong_hook_answer_does_not_push_cards_to_tomorrow(env):
    """Крючок — вопрос до объяснения: ошибка в нём нормальна и не значит, что понятие далось плохо."""
    st, _ = env
    tid = make_topic(st, 2)
    c = st.concept_by_slug(tid, "c0")
    sid = st.start_session("learn", tid, started=T0)
    st.log_attempt("pretest", False, grade=1, session_id=sid, concept_id=c["id"], ts=T0)
    for _ in range(2):
        st.log_attempt("practice", True, session_id=sid, concept_id=c["id"], ts=T0 + 300)
    ids = engine.add_session_items(st, tid, c["id"], [{"prompt": "q", "answer": "a"}], session_id=sid, ts=T0)
    assert 2 * DAY <= st.item(ids[0])["due"] - T0 <= 4 * DAY
    # был только крючок, без практики — проверим уже завтра
    c1 = st.concept_by_slug(tid, "c1")
    st.log_attempt("pretest", True, session_id=sid, concept_id=c1["id"], ts=T0)
    ids = engine.add_session_items(st, tid, c1["id"], [{"prompt": "q", "answer": "a"}], session_id=sid, ts=T0)
    assert st.item(ids[0])["due"] - T0 < 1.5 * DAY


def test_review_ahead_counts_for_memory_but_not_for_format_test(env):
    """Повторили сами в день урока: FSRS это учитывает, а отложенный тест по понятию уже нечистый."""
    st, settings = env
    tid = make_topic(st, 2)
    arms = {"order": "task_first", "present": "schema", "recall": "own_words", "hook": "riddle",
            "gift": "secret", "kind": "concept"}
    sid = st.start_session("learn", tid, started=T0, arms=arms)
    c0, c1 = st.concept_by_slug(tid, "c0"), st.concept_by_slug(tid, "c1")
    items = {}
    for c in (c0, c1):
        engine.introduce_concept(st, c["id"], sid, ts=T0)
        st.log_attempt("practice", True, session_id=sid, topic_id=tid, concept_id=c["id"], ts=T0 + 60)
        items[c["slug"]] = engine.add_session_items(st, tid, c["id"], [{"prompt": "q", "answer": "a"}],
                                                    session_id=sid, ts=T0 + 120)[0]
    before = st.item(items["c0"])
    res = engine.record_review(st, settings, items["c0"], fsrs.GOOD, ts=T0 + 3 * 3600, ahead=True)
    after = st.item(items["c0"])
    assert res["phase"] == "ahead"
    assert after["reps"] == before["reps"] + 1 and after["stability"] > before["stability"]
    assert after["due"] - (T0 + 3 * 3600) >= 3 * DAY           # вспомнили — срок сдвинулся вперёд, а не назад
    # плановое повторение через несколько дней — не отложенный тест (понятие уже повторяли), награды нет
    res = engine.record_review(st, settings, items["c0"], fsrs.GOOD, ts=T0 + 5 * DAY)
    assert res["phase"] == "review"
    assert st.arm_stats("order", "concept").get("task_first", {}).get("n", 0) == 0
    # повторили сами, но уже через 2 дня после урока — это честный отложенный тест
    res = engine.record_review(st, settings, items["c1"], fsrs.GOOD, ts=T0 + 2 * DAY, ahead=True)
    assert res["phase"] == "delayed"
    assert st.arm_stats("order", "concept")["task_first"]["n"] == 1


def test_concept_becomes_mastered(env):
    st, settings = env
    tid = make_topic(st, 1)
    c = st.concept_by_slug(tid, "c0")
    sid = st.start_session("learn", tid, started=T0)
    engine.introduce_concept(st, c["id"], sid, ts=T0)
    ids = engine.add_session_items(st, tid, c["id"], [{"prompt": "q", "answer": "a"}], session_id=sid, ts=T0)
    t = T0
    for _ in range(4):
        t = st.item(ids[0])["due"]
        engine.record_review(st, settings, ids[0], fsrs.GOOD, ts=t)
    assert st.concept(c["id"])["status"] == "mastered"
    assert engine.topic_progress(st, tid, t)["mastered"] == 1


# ---------------------------------------------------------------- план
def test_plan_respects_prereqs_and_budget(env):
    st, settings = env
    tid = make_topic(st, 4)
    settings.set("learn.session_minutes", 20)
    plan = planner.plan_today(st, settings, tid, ts=T0)
    assert plan.concept["slug"] == "c0"                     # c1 ждёт c0
    for i in range(40):
        iid = st.add_item(tid, None, f"вопрос {i}", "ответ")
        st.update_item(iid, due=T0 - 3600, reps=1, stability=2.0, last_review=T0 - 3 * DAY)
    plan = planner.plan_today(st, settings, tid, ts=T0)
    assert plan.minutes <= 20 + 5 and len(plan.reviews) < 40 and plan.due_total == 40
    assert plan.concept is None or plan.minutes <= 25


def test_tough_day_and_return_after_miss(env):
    st, settings = env
    tid = make_topic(st, 2)
    for i in range(30):
        iid = st.add_item(tid, None, f"q{i}")
        st.update_item(iid, due=T0 - 60, reps=1, stability=1.0, last_review=T0 - 2 * DAY)
    normal = planner.plan_today(st, settings, tid, ts=T0)
    talk = st.start_session("talk", None, started=T0 - 3600, mood_before=2, mode="listen")
    st.update_session(talk, ended=T0 - 1800, mood_after=5)
    tough = planner.plan_today(st, settings, tid, ts=T0)
    assert tough.kind == "short" and tough.budget == pytest.approx(normal.budget / 2)
    st.execute("DELETE FROM sessions")
    s = st.start_session("review", None, started=T0 - 2 * DAY)
    st.update_session(s, ended=T0 - 2 * DAY + 600, active_ms=600_000)
    ret = planner.plan_today(st, settings, tid, ts=T0)
    assert ret.kind == "return" and ret.minutes <= 5 + 1 and ret.concept is None


# ---------------------------------------------------------------- серия, усталость, мотивация
def study(st, ts, minutes=10, **fields):
    sid = st.start_session("learn", None, started=ts, **fields)
    st.update_session(sid, ended=ts + minutes * 60, active_ms=minutes * 60_000)
    return sid


def test_streak_with_freeze(env):
    st, _ = env
    for d in (1, 2, 4, 5, 6):                                    # пропуск на 3-й день назад
        study(st, T0 - d * DAY)
    s = metrics.streak(st, T0, freezes_per_week=1)
    assert s["days"] == 5 and not s["studied_today"] and len(s["frozen"]) == 1
    s0 = metrics.streak(st, T0, freezes_per_week=0)
    assert s0["days"] == 2
    study(st, T0)
    assert metrics.streak(st, T0, 1)["studied_today"]


def test_fatigue_point_found(env):
    """Первые 20 минут — 90 % верно, дальше — 40 %: сессия должна ограничиться 20 минутами."""
    st, _ = env
    rng = random.Random(3)
    for d in range(10):
        start = T0 - (d + 1) * DAY
        sid = study(st, start, minutes=40)
        for m in range(0, 40, 2):
            p = 0.9 if m < 20 else 0.4
            st.log_attempt("practice", rng.random() < p, session_id=sid, ts=start + m * 60 + 30)
    assert metrics.fatigue_minutes(st) == 20


def test_motivation_alarm_when_pull_grows_alone(env):
    st, _ = env
    for d in range(28):
        ts = T0 - (27 - d) * DAY
        recent = d >= 14
        study(st, ts, self_started=1 if recent else int(d % 2 == 0), liking=3)
        st.log_attempt("delayed", d % 3 != 0, ts=ts + 100)
    m = metrics.motivation(st, T0 + 3600)
    assert m["enough"] and m["alarm"]
    assert m["current"]["pull"] > m["previous"]["pull"]


def test_no_alarm_when_liking_grows_too(env):
    st, _ = env
    for d in range(28):
        ts = T0 - (27 - d) * DAY
        recent = d >= 14
        study(st, ts, self_started=1 if recent else int(d % 2 == 0), liking=5 if recent else 3)
    assert not metrics.motivation(st, T0 + 3600)["alarm"]


def test_reminder_trigger_detection(env):
    st, _ = env
    st.log_event("reminder", ts=T0)
    assert metrics.trigger_for(st, T0 + 1200) == T0
    assert metrics.trigger_for(st, T0 + 4 * 3600) is None
    study(st, T0 + 600)
    assert metrics.trigger_for(st, T0 + 1800) is None            # первая сессия уже «забрала» напоминание


def test_talk_firewall_passes_only_flag(env):
    st, _ = env
    sid = st.start_session("talk", None, started=T0 - 600, mode="understand", mood_before=3)
    st.update_session(sid, ended=T0, mood_after=6)
    st.add_talk_note(sid, "Разобрались с тревогой перед экзаменом", ["экзамен", "тревога"], "назвать чувство",
                     "взгляд со стороны")
    assert metrics.tough_day(st, T0)
    stats = metrics.talk_stats(st)
    assert stats["modes"]["understand"]["delta"] == 3 and ("экзамен", 1) in stats["themes"]
    # план учёбы видит только флаг: в нём нет ни слова из разговора
    plan = planner.plan_today(st, Settings(st.path.parent / "s.json"), None, ts=T0)
    assert "экзамен" not in plan.note and "тревог" not in plan.note
    assert math.isclose(plan.budget, Settings(st.path.parent / "s2.json").get("learn.session_minutes") / 2)
