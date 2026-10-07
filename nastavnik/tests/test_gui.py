"""Сценарии окна на виртуальном экране с поддельным claude: как человек пользуется «Наставником».

Каждый сценарий проверяет не только картинку, но и главное — что действие дошло до данных,
на которых подстраивается учёба (ответы, уверенность, время, оценки, самочувствие).
"""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from nastavnik import app as appmod
from nastavnik import tutor
from nastavnik.config import Settings
from nastavnik.learn import engine, fsrs
from nastavnik.storage import Storage
from nastavnik.ui.chat import AssistantMessage
from nastavnik.ui.concept_map import ConceptMap
from tests.conftest import FAKE_BIN, FAKE_CLAUDE, ROOT, TMP_HOME

os.environ["PATH"] = os.pathsep.join([str(FAKE_BIN), str(Path(sys.executable).parent), os.environ.get("PATH", "")])
QAPP = QApplication.instance() or QApplication([])
QAPP.setStyle("Fusion")
SHOTS = ROOT / "docs" / "screenshots"
PAGES = ["home", "learn", "review", "progress", "talk", "settings"]


def wait(cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        QAPP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QAPP.processEvents()
    return bool(cond())


def pump(seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QAPP.processEvents()
        time.sleep(0.01)
    QAPP.sendPostedEvents(None, QEvent.DeferredDelete)


def texts(widget) -> str:
    return "\n".join(lab.text() for lab in widget.findChildren(QLabel) if lab.isVisible())


def find_button(widget, text: str) -> QPushButton | None:
    for b in widget.findChildren(QPushButton):
        if b.isVisible() and b.text().strip() == text:
            return b
    return None


def make(tmp_path, theme="dark", welcome=True) -> tuple:
    settings = Settings(tmp_path / "settings.json")
    for key, value in (("claude.command", str(FAKE_CLAUDE)), ("ui.welcome_done", welcome), ("ui.tips_seen", PAGES),
                       ("ui.theme", theme), ("profile.interests", "игры, системы и схемы, свой сервер"),
                       ("profile.name", "Макс")):
        settings.set(key, value)
    storage = Storage(tmp_path / "db.sqlite3")
    c = appmod.Controller(QAPP, storage, settings, services=False)
    c.claude_status = {"ok": True, "installed": True, "message": "", "checked": True}
    c.start()
    c.window.resize(1180, 800)
    pump()
    return c, storage, settings


def close(c) -> None:
    c.on_close()
    c._cancel.set()
    for t in list(c._threads):
        t.join(timeout=30)
    QAPP.processEvents()
    for d in QAPP.topLevelWidgets():
        if d is not c.window and d.isVisible():
            d.close()
    c.window.hide()
    c.detach()
    c.window.deleteLater()
    QAPP.sendPostedEvents(None, QEvent.DeferredDelete)
    QAPP.processEvents()
    gc.collect()                       # мусор с Qt-объектами — в потоке окна (см. app.main_thread_gc)


@pytest.fixture()
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.delenv("FAKE_TOKENS", raising=False)
    monkeypatch.delenv("FAKE_DELAY_MS", raising=False)
    c, storage, settings = make(tmp_path)
    yield c, storage, settings
    close(c)
    storage.close()


def topic_with_map(c) -> int:
    tid = c.storage.add_topic("Компьютерные сети", "понимать, как ходит трафик", "beginner")
    tutor.build_map(c.storage, c.settings, tid)
    return tid


def chat_text(view) -> str:
    return "\n".join(view.chat.assistant_texts())


def send(view, text: str, confidence: int | None = None) -> None:
    if confidence:
        view.input.conf_buttons[confidence - 1].click()
    view.input.edit.setPlainText(text)
    QTest.keyClick(view.input.edit, Qt.Key_Return)


# ---------------------------------------------------------------- темы
def test_new_topic_gets_a_map_from_claude(gui):
    c, st, _ = gui
    tid = c.create_topic("Компьютерные сети", "понимать трафик", "beginner", "")
    assert c.window.current == "learn" and c.window.learn_page.topic_id == tid
    assert wait(lambda: len(st.concepts(tid)) == 5 and c.window.learn_page.map is not None
                and c.window.learn_page.map.isVisible())
    assert "Карта готова" in c.window.toast_box.label.text()
    m = c.window.learn_page.map
    assert len(m.rects) == 5 and m.minimumHeight() > 200        # 5 слоёв цепочкой — карта высокая
    c.window.grab().save(str(SHOTS / "topic-dark.png"))


# ---------------------------------------------------------------- сессия
def test_learning_session_from_hook_to_summary(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    c.window.open_topic(tid)
    pump()
    c.window.learn_page.start_btn.click()
    view = c.window.session_view
    assert c.window.in_focus_mode() and not c.window.sidebar.isVisible()
    assert wait(lambda: "Крючок" in chat_text(view) and not c.learn_busy)
    assert view.steps.chips["hook"].property("state") == "now"
    sid = c.learn_sid
    time.sleep(0.2)
    send(view, "Пакеты идут разными путями", confidence=3)
    assert wait(lambda: "Верно" in chat_text(view) and not c.learn_busy)
    a = st.attempts(session_id=sid)[-1]
    assert a["confidence"] == 3 and a["correct"] == 1 and a["latency_ms"] >= 150
    assert "скорее уверен" in texts(view)                   # уверенность видна в облачке ответа
    view.end_btn.click()
    assert wait(lambda: c.learn_phase == "rating" and hasattr(view, "stars"))
    pump(0.2)
    assert "петля" in view.steps.chips["loop"].text().lower() and view.steps.chips["loop"].property("state") == "now"
    c.window.grab().save(str(SHOTS / "session-dark.png"))
    view.stars[4].click()
    assert wait(lambda: c.learn_phase == "done")
    s = st.session(sid)
    assert s["liking"] == 5 and s["ended"] and s["active_ms"] > 0
    assert "Теперь вы можете" in texts(view) and "Почему TCP тормозит" in texts(view)
    assert len(st.items(topic_id=tid)) == 2
    find_button(view, "Вернуться к теме").click()
    pump()
    assert c.window.current == "learn" and c.window.sidebar.isVisible()
    c.window.open_page("home")
    pump()
    assert "Почему TCP тормозит при потерях?" in texts(c.window.home_page.area)


def test_streaming_answer_does_not_freeze_window(gui, monkeypatch):
    """Ответ идёт кусками по 120 мс; самая длинная пауза интерфейса должна быть короткой."""
    c, st, _ = gui
    monkeypatch.setenv("FAKE_DELAY_MS", "120")
    tid = topic_with_map(c)
    gaps, last = [], [time.perf_counter()]

    def beat():
        t = time.perf_counter()
        gaps.append(t - last[0])
        last[0] = t
    timer = QTimer()
    timer.timeout.connect(beat)
    timer.start(10)
    c.start_learning(tid)
    seen = []
    assert wait(lambda: (seen.append(len(chat_text(c.window.session_view))) or True) and not c.learn_busy
                and "Крючок" in chat_text(c.window.session_view))
    timer.stop()
    assert max(gaps) < 0.25, f"окно замирало на {max(gaps):.3f} с"
    assert len({n for n in seen if n}) >= 3                 # текст рос по частям, а не появился разом


def test_leave_without_summary(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    c.start_learning(tid)
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(c.window.session_view))
    sid = c.learn_sid
    c.learn_leave()
    pump()
    dlg = next(d for d in QAPP.topLevelWidgets() if d.isVisible() and d.windowTitle() == "Закончить сессию?")
    find_button(dlg, "Выйти без итога").click()
    pump()
    s = st.session(sid)
    assert s["ended"] and s["liking"] is None and c.learn_sid is None and c.window.current == "learn"


def test_claude_login_problem_is_fixable_from_chat(gui, monkeypatch):
    c, st, _ = gui
    tid = topic_with_map(c)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "auth")
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: find_button(view, "Войти в Claude") is not None)
    assert "Вход в Claude истёк" in c.notices["claude"][1]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    find_button(view, "Повторить").click()
    assert wait(lambda: "Крючок" in chat_text(view) and not c.learn_busy)
    assert len([m for m in st.messages(c.learn_sid) if m["role"] == "app"]) == 1    # повтор не задвоил команду


def test_new_conversation_note_after_context_limit(gui, monkeypatch):
    c, st, settings = gui
    tid = topic_with_map(c)
    settings.set("learn.context_tokens", 8000)
    monkeypatch.setenv("FAKE_TOKENS", "9000")
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    send(view, "Пакеты")
    assert wait(lambda: not c.learn_busy and "Верно" in chat_text(view))
    pump(0.1)
    assert "Начат новый разговор с Claude" in texts(view)


# ---------------------------------------------------------------- повторение
def review_items(st, tid, n=3, answer="Порция данных с адресом получателя", due_ago=60):
    concept = st.concept_by_slug(tid, "c0")
    engine.introduce_concept(st, concept["id"], None, ts=time.time() - 5 * 86400)
    ids = []
    for i in range(n):
        iid = st.add_item(tid, concept["id"], f"Вопрос {i}: что такое пакет?", answer)
        st.update_item(iid, due=time.time() - due_ago, reps=1, stability=3.0, difficulty=5.0,
                       last_review=time.time() - 4 * 86400, state="review")
        ids.append(iid)
    return ids


def answer_card(page, text: str) -> None:
    page.attempt.setPlainText(text)
    QTest.keyClick(page.attempt, Qt.Key_Return)              # Enter — отдать ответ на проверку


def test_review_answers_are_checked_by_claude(gui):
    """Человек пишет ответ — Claude ставит оценку и объясняет; оценка сразу в FSRS."""
    c, st, _ = gui
    tid = topic_with_map(c)
    ids = review_items(st, tid)
    c.window.open_page("review")
    page = c.window.review_page
    pump()
    assert page.card.isVisible() and "Вопрос" in page.question.text()
    assert "ПРОВЕРКА" in page.meta.text()                    # первое повторение понятия — отложенный тест
    assert not page.grades.isVisible()                       # оценку себе не ставят
    for k, text in enumerate(("порция данных с адресом получателя", "кусок данных", "не знаю, что-то про сеть")):
        time.sleep(0.15)
        answer_card(page, text)
        assert page.state == "checked" and "ЭТАЛОН" in texts(page.card) and text in texts(page.card)
        assert wait(lambda: page.verdict_title.text() and "проверяет" not in page.verdict_title.text())
        if k == 0:
            assert "Верно" in page.verdict_title.text() and "вернётся" in page.verdict_title.text()
            assert not page.feed.isVisible()                  # вердикт на карточке, в «Проверено» — после ухода
        if k == 1:
            c.window.grab().save(str(SHOTS / "review-verdict-dark.png"))
        QTest.keyClick(page.next_btn, Qt.Key_Return)          # Enter — дальше
        pump(0.05)
    tries = [a for a in st.attempts() if a["item_id"] in ids]
    assert [a["grade"] for a in tries[:3]] == [4, 2, 1]
    assert texts(page.feed).count("Вопрос") == 3
    assert all(a["phase"] == "delayed" and a["note"].startswith("Claude: ") for a in tries[:3])
    assert all(a["latency_ms"] >= 100 for a in tries[:3])
    assert st.item(ids[0])["due"] > time.time() + 86400
    # «не вспомнил» — карточка вернулась в конец этой же очереди
    assert page.current is not None and page.current["id"] == ids[2]
    page.dunno()
    assert page.state == "checked" and "Не вспомнил" in page.verdict_title.text()
    QTest.keyClick(page.next_btn, Qt.Key_Return)
    pump(0.05)
    assert page.current["id"] == ids[2]                       # и ещё раз — пока не вспомнит
    answer_card(page, "порция данных с адресом получателя")
    assert wait(lambda: "Верно" in page.verdict_title.text()), (page.state, page.verdict_title.text(), page.verdict_text.text())
    page.next_card()
    assert wait(lambda: "На сегодня всё" in page.empty.text())
    review = st.sessions(kind="review")[-1]
    assert review["ended"] and review["active_ms"] >= 0


def test_moving_on_before_verdict_still_grades(gui, monkeypatch):
    """Claude проверяет 1,5 с, а человек сразу жмёт «Дальше» — оценка всё равно встаёт, вердикт — в «Проверено»."""
    c, st, _ = gui
    monkeypatch.setenv("FAKE_CHECK_MS", "1500")
    tid = topic_with_map(c)
    ids = review_items(st, tid, n=2)
    c.window.open_page("review")
    page = c.window.review_page
    pump()
    answer_card(page, "порция данных с адресом получателя")
    assert "проверяет" in page.verdict_title.text()
    page.next_card()
    assert page.current["id"] == ids[1] and page.state == "answer"
    assert wait(lambda: page.feed.isVisible() and "Верно" in texts(page.feed), timeout=20)
    assert st.item(ids[0])["reps"] == 2 and page.state == "answer"       # текущая карточка не тронута
    answer_card(page, "порция данных с адресом получателя")
    page.next_card()
    assert "дописывает" in page.empty.text()
    assert wait(lambda: "На сегодня всё" in page.empty.text(), timeout=20)


def test_claude_unavailable_means_grading_yourself(gui, monkeypatch):
    c, st, _ = gui
    tid = topic_with_map(c)
    ids = review_items(st, tid, n=2)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    c.window.open_page("review")
    page = c.window.review_page
    pump()
    answer_card(page, "порция данных")
    assert wait(lambda: page.state == "manual")
    assert "Claude не проверил" in page.verdict_title.text() and page.grades.isVisible()
    assert page.banner.isVisible() and "Лимит" in page.banner.text()
    QTest.keyClick(page.grade_buttons[fsrs.GOOD], Qt.Key_3)
    pump(0.05)
    a = [x for x in st.attempts() if x["item_id"] == ids[0]][-1]
    assert a["grade"] == 3 and a["note"] == "своя оценка"
    assert page.current["id"] == ids[1] and page.check_btn.text().startswith("Показать ответ")
    answer_card(page, "порция")                                # дальше — сразу своя оценка, без ожидания
    assert page.state == "manual" and page.grades.isVisible()


def test_self_grading_when_check_is_off(gui):
    c, st, settings = gui
    settings.set("review.ai_check", False)
    tid = topic_with_map(c)
    ids = review_items(st, tid, n=1)
    c.window.open_page("review")
    page = c.window.review_page
    pump()
    assert page.check_btn.text().startswith("Показать ответ")
    answer_card(page, "порция данных")
    assert page.state == "manual" and not page.verdict.isVisible()
    page.grade(fsrs.EASY)
    assert [x for x in st.attempts() if x["item_id"] == ids[0]][-1]["grade"] == 4


# ---------------------------------------------------------------- разговор
def test_talk_from_mood_to_memory_and_transcript_cleanup(gui):
    c, st, _ = gui
    c.window.open_page("talk")
    page = c.window.talk_page
    pump()
    page.mood.slider.setValue(3)
    page.feeling_buttons[4].click()                          # «усталость»
    page.start_btn.click()
    view = c.window.talk_view
    assert c.window.in_focus_mode()
    send(view, "Всё валится из рук, я просто ленивый")
    assert wait(lambda: "усталость" in chat_text(view) and not c.talk_busy)
    sid = c.talk_sid
    cs = st.session(sid)["claude_session"]
    assert list((TMP_HOME / ".claude" / "projects").glob(f"*/{cs}.jsonl"))
    pump(0.2)
    c.window.grab().save(str(SHOTS / "talk-session-dark.png"))
    view.end_btn.click()
    assert wait(lambda: c.talk_phase == "mood")
    assert "Стало яснее" in chat_text(view)
    view.mood_picker.slider.setValue(7)
    view.mood_done.click()
    assert wait(lambda: hasattr(view, "keep_btn") and view.keep_btn.isVisible())
    view.memory_edit.setPlainText("Ты понял, что это усталость, а не лень.")
    view.keep_btn.click()
    assert wait(lambda: c.talk_phase == "done")
    assert "Было 3 → стало 7" in texts(view) and "Переписка удалена" in texts(view)
    s = st.session(sid)
    assert s["mood_before"] == 3 and s["mood_after"] == 7 and s["feeling"] == "усталость"
    assert st.talk_notes()[0]["summary"] == "Ты понял, что это усталость, а не лень."
    assert not list((TMP_HOME / ".claude" / "projects").glob(f"*/{cs}.jsonl"))
    assert not st.messages(sid)
    find_button(view, "Вернуться").click()
    pump()
    assert "+4.0" in texts(c.window.talk_page.area) and "Ты понял" in texts(c.window.talk_page.area)


def test_tough_talk_makes_study_plan_shorter_without_content(gui):
    c, st, settings = gui
    tid = topic_with_map(c)
    c.talk_start("listen", 2, "тревога")
    send(c.window.talk_view, "Завтра защита диплома, мне страшно")
    assert wait(lambda: not c.talk_busy and "усталость" in chat_text(c.window.talk_view))
    c.talk_close()
    c.window.open_page("home")
    pump()
    home = texts(c.window.home_page.area)
    assert "Сегодня можно коротко" in home and c.window.home_page.start_btn.text() == "Короткая сессия"
    assert "диплом" not in home and "страшно" not in home
    plan = c.plan_for(tid)
    assert plan.budget == pytest.approx(settings.get("learn.session_minutes") / 2)


# ---------------------------------------------------------------- прогресс, настройки, мастер
def test_progress_shows_what_works_and_alarm(gui):
    c, st, _ = gui
    for _ in range(12):
        st.update_arm("order", "concept", "task_first", 1.0)
    for _ in range(10):
        st.update_arm("order", "concept", "theory_first", 0.0)
    now = time.time()
    for d in range(28):
        ts = now - (27 - d) * 86400
        sid = st.start_session("learn", None, started=ts, self_started=1 if d >= 14 else int(d % 2 == 0), liking=3)
        st.update_session(sid, ended=ts + 900, active_ms=900_000)
    c.window.open_page("progress")
    pump()
    text = texts(c.window.progress_page.area)
    assert "Сначала задача, потом объяснение  ← лучше всего" in text
    assert "Тяга растёт, а удовольствие и польза — нет" in text
    c.window.grab().save(str(SHOTS / "progress-dark.png"))


def test_welcome_wizard_saves_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    from nastavnik.ui.welcome import Welcome
    c, st, settings = make(tmp_path, welcome=False)
    w = Welcome(c, c.window)
    w.show()
    w.next.click()
    assert wait(lambda: "Вход выполнен" in w.claude_label.text() or c.claude_status.get("checked"))
    w.next.click()
    w.name.setText("Макс")
    w.interests.setText("системы, игры")
    w.next.click()
    w.first_topic.setChecked(False)
    w.next.click()
    assert settings.get("ui.welcome_done") and settings.get("profile.interests") == "системы, игры"
    close(c)
    st.close()


def test_broken_settings_and_database_heal_themselves(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    (tmp_path / "settings.json").write_text("{не json", encoding="utf-8")
    db = tmp_path / "db.sqlite3"
    db.write_bytes(b"SQLite format 3\x00" + b"\x13" * 4000)
    settings = Settings(tmp_path / "settings.json")
    assert settings.get("learn.session_minutes") == 25
    assert list(tmp_path.glob("settings.json.broken-*"))
    settings.set("learn.session_minutes", 9999)
    assert Settings(tmp_path / "settings.json").get("learn.session_minutes") == 120   # диапазон соблюдается
    storage = Storage(db)
    assert storage.problem and list(tmp_path.glob("db.sqlite3.broken-*"))
    settings.set("claude.command", str(FAKE_CLAUDE))
    settings.set("ui.welcome_done", True)
    c = appmod.Controller(QAPP, storage, settings, services=False)
    c.start()
    pump()
    assert "storage" in c.notices
    assert "новая" in c.notices["storage"][1] or "восстановлена" in c.notices["storage"][1]
    close(c)
    storage.close()


def test_home_is_fast_with_a_year_of_data(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    now = time.time()
    with st._lock:
        for i in range(2000):
            st.db.execute("INSERT INTO items(topic_id, concept_id, prompt, answer, created_at, state, stability, "
                          "difficulty, due, last_review, reps) VALUES (?, NULL, ?, 'a', ?, 'review', ?, 5, ?, ?, 3)",
                          (tid, f"q{i}", now - 300 * 86400, 5 + i % 40, now + (i % 30 - 5) * 86400,
                           now - (i % 20) * 86400))
        for i in range(6000):
            st.db.execute("INSERT INTO attempts(ts, phase, correct, grade, latency_ms) VALUES (?, 'review', ?, 3, 5000)",
                          (now - (i % 365) * 86400, i % 5 != 0))
        for d in range(365):
            st.db.execute("INSERT INTO sessions(kind, started, ended, active_ms, self_started) "
                          "VALUES ('learn', ?, ?, 1200000, 1)", (now - d * 86400, now - d * 86400 + 1200))
        st.db.commit()
    started = time.perf_counter()
    c.window.open_page("home")
    QAPP.processEvents()
    took = time.perf_counter() - started
    assert took < 1.5, f"Главная открывалась {took:.2f} с"
    assert "365" in texts(c.window.home_page.area)          # серия за год посчитана


# ---------------------------------------------------------------- скриншоты
def seed_history(st) -> None:
    """Три недели занятий и экспериментов — чтобы на скриншотах было что показать."""
    import random
    rng = random.Random(5)
    now = time.time()
    for d in range(21):
        if d in (6, 13):
            continue
        ts = now - (d + 1) * 86400 + 19 * 3600 % 86400
        sid = st.start_session("learn", None, started=ts, self_started=int(rng.random() < 0.7),
                               liking=rng.choice([3, 4, 4, 5]))
        st.update_session(sid, ended=ts + 1500, active_ms=rng.randint(12, 28) * 60_000)
        for k in range(3):
            st.log_attempt("delayed", rng.random() < 0.8, ts=ts + 60 * k)
    for arm, p in (("task_first", 0.82), ("theory_first", 0.6)):
        for _ in range(9):
            st.update_arm("order", "concept", arm, 1.0 if rng.random() < p else 0.0)
    for arm, p in (("schema_recall", 0.85), ("own_words", 0.7), ("transfer", 0.65)):
        for _ in range(5):
            st.update_arm("recall", "concept", arm, 1.0 if rng.random() < p else 0.0)
    talk = st.start_session("talk", None, started=now - 3 * 86400, mode="listen", mood_before=3)
    st.update_session(talk, ended=now - 3 * 86400 + 900, mood_after=6)
    st.add_talk_note(talk, "Ты понял, что усталость — не лень, и что отдых тоже часть дела.", ["усталость", "учёба"],
                     "назвать чувство", "назвать чувство")

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_screenshots_of_all_screens(tmp_path, monkeypatch, theme):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    c, st, _ = make(tmp_path, theme=theme)
    tid = topic_with_map(c)
    c0 = st.concept_by_slug(tid, "c0")
    st.update_concept(c0["id"], status="mastered")
    st.update_concept(st.concept_by_slug(tid, "c1")["id"], status="learning")
    st.add_checkpoint(tid, None, "Пакеты и адреса", "маски подсетей", "Почему TCP тормозит при потерях?",
                      "объяснить путь пакета")
    for i in range(4):
        iid = st.add_item(tid, c0["id"], f"Что делает маршрутизатор? ({i + 1})", "Выбирает, куда отправить пакет.")
        st.update_item(iid, due=time.time() - 60, reps=2, stability=4, difficulty=5, last_review=time.time() - 5 * 86400)
    seed_history(st)
    for key in PAGES:
        c.window.open_page(key)
        pump(0.3)
        c.window.grab().save(str(SHOTS / f"{key}-{theme}.png"))
    c.window.open_topic(tid)
    pump(0.3)
    c.window.grab().save(str(SHOTS / f"topic-{theme}.png"))
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    view.chat.add_user("Наверное, пакеты идут разными путями, и если один путь сломан — выбирают другой", 3)
    view.chat.add_assistant("**Верно**: путь выбирается заново на каждом узле.\n\nВот схема:\n\n"
                            "```\nты ──► роутер ──► провайдер ──► сайт\n          └──► запасной путь ─┘\n```\n\n"
                            "- каждый узел смотрит только на адрес\n- общего плана маршрута нет\n\n"
                            "Почему тогда пакеты могут прийти не по порядку?")
    pump(0.3)
    c.window.grab().save(str(SHOTS / f"session-{theme}.png"))
    # доска рядом с чатом: эталон Claude схемой слева, ваш рисунок — справа
    import random
    from tests.test_board import draw_request_schema
    view.chat.add_assistant("Сравни с эталоном:\n\n```mermaid\nflowchart LR\n  A[Просьба] --> B{Срочно?}\n"
                            "  B -->|да| C[Согласие]\n  B -->|нет| D([Проверка])\n```\n\n"
                            "Теперь **нарисуй по памяти**, как работает социальная инженерия: от просьбы до согласия.")
    view.after_answer(view.chat.assistant_texts()[-1], "recall")
    view.open_board()
    draw_request_schema(view.board.canvas, random.Random(3))
    pump(0.9)                                    # подсветка распознанного успевает погаснуть
    c.window.grab().save(str(SHOTS / f"board-session-{theme}.png"))
    assert all(p.exists() for p in SHOTS.glob(f"*-{theme}.png"))
    close(c)
    st.close()


def test_one_lesson_counts_as_studied_right_away(gui):
    """После одного урока тема не должна выглядеть пустой: «пройдено 1», «закреплено» — после повторений."""
    from nastavnik.ui.pages import ProgressBar
    c, st, _ = gui
    tid = topic_with_map(c)
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    send(view, "Пакеты идут разными путями", confidence=3)
    assert wait(lambda: not c.learn_busy and "Верно" in chat_text(view))
    view.end_btn.click()
    assert wait(lambda: c.learn_phase == "rating" and hasattr(view, "stars"))
    view.stars[3].click()
    assert wait(lambda: c.learn_phase == "done")
    p = engine.topic_progress(st, tid)
    assert p["studied"] == 1 and p["mastered"] == 0 and p["next_review"] is not None
    c.learn_close()
    c.window.learn_page.show_list()
    pump(0.2)
    page = c.window.learn_page
    text = texts(page.area)
    assert "Пройдено 1 из 5 · закреплено 0" in text and "следующее повторение" in text
    bars = [b for b in page.area.findChildren(ProgressBar) if b.isVisible()]
    assert bars and bars[0].studied == 0.2 and bars[0].mastered == 0.0     # полоса не пустая
    c.window.open_page("home")
    pump(0.2)
    assert "понятий пройдено · закреплено 0" in texts(c.window.home_page.area)
    c.window.open_page("progress")
    pump(0.2)
    assert "пройдено 1 из 5 · закреплено 0" in texts(c.window.progress_page.area)


def test_review_ahead_when_nothing_is_due(gui):
    """«Повторять сейчас нечего», а карточки есть — их видно на странице темы и можно повторить заранее."""
    c, st, _ = gui
    tid = topic_with_map(c)
    c0 = st.concept_by_slug(tid, "c0")
    st.update_concept(c0["id"], status="learning", introduced_at=time.time() - 3600)
    ids = []
    for k, prompt in enumerate(("Что делает маршрутизатор?", "Нарисуй по памяти путь пакета")):
        iid = st.add_item(tid, c0["id"], prompt, "Выбирает, куда отправить пакет.", kind="schema" if k else "card")
        st.update_item(iid, due=time.time() + (1 + k) * 86400, reps=1, stability=1.2 + k, difficulty=5,
                       last_review=time.time() - 3600)
        ids.append(iid)
    c.window.open_review()
    pump(0.2)
    page = c.window.review_page
    assert page.current is None and "Повторять сейчас нечего" in page.empty.text()
    assert page.ahead_btn.isVisible() and "2 карточки" in page.ahead_btn.text()
    page.ahead_btn.click()
    pump(0.1)
    assert page.ahead and page.current["id"] == ids[0]           # сначала та, что ближе к сроку
    assert "ЗАРАНЕЕ · ПО ПЛАНУ ЗАВТРА" in page.meta.text() and page.subtitle.text() == "Заранее · осталось 2"
    answer_card(page, "Выбирает, куда отправить пакет")
    assert wait(lambda: "Верно" in page.verdict_title.text())
    a = st.attempts()[-1]
    assert a["phase"] == "ahead" and a["item_id"] == ids[0]
    assert st.item(ids[0])["reps"] == 2
    page.next_card()
    answer_card(page, "Выбирает, куда отправить пакет")
    assert wait(lambda: "Верно" in page.verdict_title.text())
    page.next_card()
    pump(0.1)
    assert page.current is None and "Готово — всё повторили заранее" in page.empty.text()
    assert not page.ahead_btn.isVisible()
    # на странице темы — список карточек со сроками и кнопка «Повторить заранее»
    c.window.open_topic(tid)
    pump(0.2)
    area = texts(c.window.learn_page.area)
    assert "карточки для повторения · 2" in area.lower() and "Что делает маршрутизатор?" in area
    assert "схема по памяти · повторение" in area
    find_button(c.window.learn_page.area, "Повторить заранее (2)").click()
    pump(0.1)
    assert c.window.current == "review" and page.ahead and page.current is not None
