"""Рекомендации: учатся ли они на самом деле (симулированный читатель) и соблюдают ли правила."""
from __future__ import annotations

import time

import pytest

from svodka.rank import calibrate
from svodka.rank.features import CONTENT_TYPES
from svodka.rank.model import InterestModel
from svodka.rank.ranker import Ranker
from svodka.rank.signals import collect_signals
from svodka.storage import Storage
from tests import simulate as S

SEEDS = (1, 2, 3, 4)


def mean(xs):
    return sum(xs) / len(xs)


@pytest.fixture(scope="module")
def runs():
    out = {"pers": [], "base": [], "shift": [], "shift_base": []}
    for seed in SEEDS:
        out["pers"].append(S.simulate(days=28, seed=seed))
        out["base"].append(S.simulate(days=28, seed=seed, personalization=False))
        out["shift"].append(S.simulate(days=28, seed=seed, shift_day=14))
        out["shift_base"].append(S.simulate(days=28, seed=seed, shift_day=14, personalization=False))
    return out


def test_personalization_beats_baseline(runs):
    pers = mean([p for r in runs["pers"] for p in r["precision"][14:]])
    base = mean([p for r in runs["base"] for p in r["precision"][14:]])
    assert pers >= base + 0.12, (pers, base)


def test_learning_curve_rises(runs):
    week1 = mean([p for r in runs["pers"] for p in r["precision"][:7]])
    later = mean([p for r in runs["pers"] for p in r["precision"][14:]])
    assert later >= week1 + 0.06, (week1, later)


def test_exploration_finds_hidden_interest_and_drops_disliked(runs):
    bio = [r["model"].stats["topic:Биотех"].mean() for r in runs["pers"]]
    sport = [r["model"].stats["topic:Спорт"].mean() for r in runs["pers"]]
    assert sum(b > 0.55 for b in bio) >= 3, bio
    assert sum(s < 0.45 for s in sport) >= 3, sport


def test_adapts_after_interest_shift(runs):
    space = [r["model"].stats["topic:Космос"].mean() for r in runs["shift"]]
    assert sum(s > 0.55 for s in space) >= 3, space
    after = mean([p for r in runs["shift"] for p in r["precision"][21:]])
    base = mean([p for r in runs["shift_base"] for p in r["precision"][21:]])
    assert after >= base + 0.1, (after, base)


def test_main_never_repeats_a_story(runs):
    assert sum(r["story_violations"] for r in runs["pers"] + runs["shift"]) == 0


def test_main_keeps_exploration(runs):
    first_week = [n for r in runs["pers"] for n in r["explore_in_main"][:7]]
    assert mean(first_week) >= 1.0


def test_learned_mix_drops_noise_part(runs):
    """В симуляции «важность» — чистый шум для читателя: лента должна это заметить."""
    st = Storage(runs["pers"][0]["db"])
    rk = Ranker(st, S.SimSettings(), now=S.START + 28 * S.DAY)
    assert rk.mix_report.get("n", 0) >= 40
    assert rk.weights["importance"] < 0.1
    assert rk.weights["personal"] > rk.weights["fit"]


# ---------------------------------------------------------------- правила и устойчивость
def _store(tmp_path) -> Storage:
    return Storage(tmp_path / "t.sqlite3")


def _article(i: int, **kw) -> dict:
    a = {"url": f"https://site{i % 3}.com/a{i}", "title_ru": f"Статья {i}", "topic": "ИИ: развитие",
         "entities": ["OpenAI"], "kind": "analysis", "words": 1200, "fit": 0.6, "importance": 0.5,
         "collected_at": time.time() - 3600}
    a.update(kw)
    return a


def test_muted_source_never_shown(tmp_path):
    st = _store(tmp_path)
    for i in range(12):
        st.add_article(_article(i))
    st.add_rule("mute_source", "site1.com")
    feed = Ranker(st).build()
    assert feed.all_items()
    assert all(it.article["domain"] != "site1.com" for it in feed.all_items())


def test_week_old_article_never_in_main(tmp_path):
    """Собрано сегодня, но опубликовано неделю назад — это не свежее: ни в «Главном», ни в «Ещё свежем»."""
    st = _store(tmp_path)
    for i in range(8):
        st.add_article(_article(i, published_at=time.time() - 3 * 3600))
    old = st.add_article(_article(99, published_at=time.time() - 8 * 86400, fit=1.0, importance=1.0))
    feed = Ranker(st).build()
    assert old not in {it.id for it in feed.main + feed.more}
    assert old in {it.id for _d, items in feed.days for it in items}              # в архиве дня — пожалуйста


def test_feed_is_stable_within_a_day(tmp_path):
    st = _store(tmp_path)
    for i in range(15):
        st.add_article(_article(i, entities=[f"E{i}"], fit=0.3 + i / 30))
    now = time.time()
    a = [it.id for it in Ranker(st, now=now).build().main]
    b = [it.id for it in Ranker(st, now=now + 60).build().main]
    assert a == b


def test_signal_routing(tmp_path):
    """«Не моя тема» бьёт по содержанию, «уже знал» — только по источнику, кликбейт — сильно по источнику."""
    st = _store(tmp_path)
    ids = [st.add_article(_article(i)) for i in range(3)]
    st.log_event(ids[0], "dislike", 1)
    st.log_event(ids[1], "known", 1)
    st.log_event(ids[2], "clickbait", 1)
    sig = collect_signals(st)
    dis, known, bait = sig[ids[0]], sig[ids[1]], sig[ids[2]]
    assert all(c.scope <= CONTENT_TYPES for c in dis.contribs) and dis.value < -2
    assert all(c.scope == frozenset({"source"}) for c in known.contribs) and known.value == 0
    assert sum(c.amount for c in bait.contribs if "source" in c.scope) <= -5


def test_toggle_off_reaction_is_forgotten(tmp_path):
    st = _store(tmp_path)
    aid = st.add_article(_article(1))
    st.log_event(aid, "like", 1)
    st.log_event(aid, "like", 0)
    assert collect_signals(st)[aid].contribs == []


def test_skipped_search_result_is_not_a_dislike(tmp_path):
    """Пропуск в ленте — слабый минус; пропуск в результатах поиска — не сигнал вовсе."""
    st = _store(tmp_path)
    feed_aid = st.add_article(_article(1))
    search_aid = st.add_article(_article(2))
    st.log_impression(feed_aid, 0, 3000)
    st.log_impression(search_aid, 0, 3000, surface="search")
    sig = collect_signals(st)
    assert sig[feed_aid].implicit.get("skipped")
    assert search_aid not in sig or not sig[search_aid].contribs


def test_purge_keeps_what_was_learned(tmp_path):
    st = _store(tmp_path)
    old = time.time() - 40 * 86400
    aid = st.add_article(_article(1, collected_at=old, entities=["Anthropic"]))
    other = st.add_article(_article(2, collected_at=old, entities=["Кто-то"]))   # для контраста: пропущена
    st.set_blocks(aid, [{"type": "p", "text": "текст"}])
    st.log_impression(other, 0, 3000, ts=old + 50)
    st.log_read(aid, 300_000, 0.95, 360_000, ts=old + 100)
    st.log_event(aid, "like", 1, ts=old + 120)
    before = InterestModel.build(st).stats["entity:anthropic"].beta
    assert st.purge(keep_days=30) == 2
    assert st.blocks(aid) == []
    after = InterestModel.build(st).stats["entity:anthropic"].beta
    assert after == pytest.approx(before) and after > 0


def test_survey_calibration_moves_expand_weight(tmp_path):
    """Если раскрытые в ленте статьи вы оцениваете высоко, вес «раскрыл» растёт."""
    st = _store(tmp_path)
    for i in range(24):
        aid = st.add_article(_article(i))
        if i % 2 == 0:
            st.log_event(aid, "expand")
            st.log_survey(aid, 5)
        else:
            st.log_survey(aid, 2)
    new, report = calibrate.propose_weights(st)
    assert report["expand"]["factor"] > 1.0
    assert new["expand"] > 0.2


def test_corrupted_database_is_recovered(tmp_path):
    path = tmp_path / "bad.sqlite3"
    path.write_bytes(b"this is not a database at all" * 100)
    st = Storage(path)
    assert st.topics()            # стартовые темы на месте
    assert "повреждена" in st.problem
