"""Сценарии окна (на виртуальном экране, с поддельным claude): как человек пользуется «Сводкой».

Каждый сценарий проверяет не только картинку, но и главное — что действие дошло до обучения ленты.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication

from svodka import app as appmod
from svodka import search as libsearch
from svodka.config import Settings
from svodka.storage import Storage
from tests.conftest import FAKE_CLAUDE
from tests.web import Site

os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)
QAPP = QApplication.instance() or QApplication([])

TOPICS = ["ИИ: развитие", "Новые технологии", "Китай: внутренняя политика", "США: внутренняя политика",
          "Россия: внутренняя политика"]
BODY = ("The new model was trained on a cluster of 100000 accelerators and released in 2026. "
        "Regulators in three countries asked for an audit of its safety evaluations before deployment.")


def wait(cond, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        QAPP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QAPP.processEvents()
    return bool(cond())


def add_articles(storage: Storage, n: int = 12) -> list[int]:
    now = time.time()
    ids = []
    for i in range(n):
        aid = storage.add_article({
            "url": f"https://site{i % 4}.example/a{i}", "source": f"Сайт {i % 4}", "lang": "en",
            "title_ru": f"Статья номер {i} про нейросети и регулирование", "title_orig": f"Article {i}",
            "summary_ru": [f"Главный факт статьи {i}.", "Второй факт."], "why_ru": "по вашей теме",
            "topic": TOPICS[i % len(TOPICS)], "subtopic": f"подтема {i % 3}", "entities": [f"Герой {i % 5}"],
            "kind": "analysis", "bucket": "core", "importance": 0.4 + 0.04 * i, "fit": 0.5 + 0.03 * i,
            "words": 900, "story_key": f"story-{i}", "published_at": now - 3600 * (i + 1),
            "collected_at": now - 600, "extract_status": "ok"})
        storage.set_blocks(aid, [{"type": "h2", "text": "Context"}, {"type": "p", "text": BODY},
                                 {"type": "p", "text": BODY + " Second paragraph."},
                                 {"type": "code", "text": "print(1)"}])
        libsearch.reindex(storage, aid)
        ids.append(aid)
    return ids


@pytest.fixture()
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.setenv("FAKE_URLS", "[]")
    settings = Settings(tmp_path / "settings.json")
    for key, value in (("claude.command", str(FAKE_CLAUDE)), ("translate.prefetch_top", 0),
                       ("ui.welcome_done", True), ("ui.tips_seen", ["feed", "search", "saved", "settings"]),
                       ("ui.theme", "dark"), ("learning.randomize_top_prob", 0.0), ("collect.mode", "agent")):
        settings.set(key, value)
    storage = Storage(tmp_path / "db.sqlite3")
    ids = add_articles(storage)
    c = appmod.Controller(QAPP, storage, settings, services=False)
    c.claude_status = {"ok": True, "installed": True, "message": "", "checked": True}
    c.start()
    c.window.resize(1180, 800)
    assert wait(lambda: c.feed is not None)
    yield c, storage, settings, ids
    close_controller(c)
    storage.close()


def close_controller(c) -> None:
    """Дождаться фоновых задач и удалить окно до закрытия базы (иначе таймеры старого окна
    сработают на закрытой базе в следующем тесте)."""
    c._cancel.set()
    for t in list(c._threads):
        t.join(timeout=30)
    QAPP.processEvents()
    for d in QAPP.topLevelWidgets():
        if d is not c.window and d.isVisible():
            d.close()
    c.window.hide()
    c.window.deleteLater()
    QAPP.sendPostedEvents(None, QEvent.DeferredDelete)
    QAPP.processEvents()


def item_rows(lst):
    return [r for r in lst.model().rows if r["kind"] == "item"]


# ---------------------------------------------------------------- лента
def test_feed_shows_main_and_more(gui):
    c, storage, _s, ids = gui
    rows = c.window.feed_page.list.model().rows
    titles = [r["title"] for r in rows if r["kind"] == "section"]
    assert titles[0] == "ГЛАВНОЕ"
    assert len(c.feed.main) == 5 and len({it.article["story_key"] for it in c.feed.main}) == 5
    assert len(item_rows(c.window.feed_page.list)) == len(ids)
    assert "новых: 12" in c.window.feed_page.subtitle.text()
    assert not c.window.feed_page.empty.isVisible()


def test_impressions_are_logged_with_scoring_parts(gui, monkeypatch):
    c, storage, _s, _ids = gui
    lst = c.window.feed_page.list
    c.window.show()
    monkeypatch.setattr(c.window, "isActiveWindow", lambda: True)
    for _ in range(3):
        lst._track()
    rows = storage.query("SELECT article_id, position, parts FROM impressions")
    assert rows, "показы не записались"
    parts = json.loads(rows[0]["parts"])
    assert {"personal", "fit", "importance", "fresh"} <= set(parts)
    assert rows[0]["position"] == 0


def test_hide_from_feed_teaches_and_can_be_undone(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[0].id
    c.hide_article(aid, "dislike")
    assert aid not in {r["item"].id for r in item_rows(c.window.feed_page.list)}
    assert storage.article(aid)["status"] == "hidden"
    assert c.reaction_state(aid).get("dislike")
    assert c.window.toast_box.isVisible() and c.window.toast_box.action.isVisible()
    c.window.toast_box.action.click()                       # «Отменить»
    assert storage.article(aid)["status"] == "new"
    assert not c.reaction_state(aid).get("dislike")
    assert wait(lambda: aid in {r["item"].id for r in item_rows(c.window.feed_page.list)})


def test_like_in_feed_marks_row_and_logs_event(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[1].id
    c.feed_action(aid, "like", QPoint(0, 0))
    assert c.reaction_state(aid).get("like")
    row = next(r for r in item_rows(c.window.feed_page.list) if r["item"].id == aid)
    assert row["item"].article.get("_liked")
    c.feed_action(aid, "like", QPoint(0, 0))                # повторное нажатие снимает отметку
    assert not c.reaction_state(aid).get("like")


def test_mute_source_removes_it_from_feed(gui):
    c, storage, _s, _ids = gui
    domain = c.feed.main[0].article["domain"]
    c.add_rule("mute_source", domain)
    assert wait(lambda: c.feed is not None and all(it.article["domain"] != domain for it in c.feed.all_items()))
    c.window.toast_box.action.click()                       # отменить правило
    assert not storage.active_rules()
    assert wait(lambda: any(it.article["domain"] == domain for it in c.feed.all_items()))


def test_save_appears_in_saved_page(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[2].id
    c.feed_action(aid, "save", QPoint(0, 0))
    assert storage.article(aid)["saved"] == 1
    c.window.open_page("saved")
    assert [r["item"].id for r in item_rows(c.window.saved_page.list)] == [aid]
    assert not c.window.saved_page.empty.isVisible()


# ---------------------------------------------------------------- читалка
def test_reader_translates_streaming_and_records_reading(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[0].id
    c.open_article(aid)
    reader = c.window.reader
    assert not c.window.sidebar.isVisible() and reader.article["id"] == aid
    assert wait(lambda: storage.article(aid)["translate_status"] == "done")
    assert wait(lambda: not reader.translating)
    kinds = [b["type"] for b in reader.shown]
    assert kinds == ["h2", "p", "p", "code"]                  # только русский текст, код — как в оригинале
    texts = [b["text"] for b in reader.shown]
    assert all("Русский перевод" in t for t in texts[:3]) and texts[3] == "print(1)"
    assert not hasattr(reader, "seg")                         # переключателя «Оригинал» больше нет
    assert not reader.status.isVisible()
    # повторное открытие — без нового перевода, из сохранённого
    calls = []
    c.translate = lambda *a, **k: calls.append(a)
    reader.open(aid)
    assert [b["type"] for b in reader.shown] == kinds and not calls
    reader.active_ms = 200_000                               # прочитали внимательно
    reader.max_scroll = 1.0
    reader.back.emit()
    assert c.window.sidebar.isVisible()
    r = storage.one("SELECT active_ms, scroll_pct, mode FROM reads WHERE article_id=?", (aid,))
    assert r["active_ms"] == 200_000 and r["scroll_pct"] == 1.0 and r["mode"] == "ru"
    assert storage.article(aid)["status"] == "read"
    # модель сразу видит прочтение
    sig = c.ranker().model.signals[aid]
    assert sig.implicit.get("read_score", 0) >= 0.6


def test_short_look_is_not_a_read(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[1].id
    c.open_article(aid)
    c.window.reader.active_ms = 3000
    c.window.reader.max_scroll = 0.1
    c.window.close_reader()
    assert storage.article(aid)["status"] == "opened"         # ушла из «Главного», но не «прочитана»
    assert c.ranker().model.signals[aid].implicit.get("short_click")


def test_reader_reactions_are_mutually_exclusive(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[0].id
    c.open_article(aid)
    reader = c.window.reader
    reader.pills["like"].click()
    assert c.reaction_state(aid)["like"]
    reader.pills["dislike"].click()
    state = c.reaction_state(aid)
    assert state["dislike"] and not state["like"] and not reader.pills["like"].isChecked()
    reader.pills["follow"].click()
    assert storage.article(aid)["followed"] == 1


def test_save_in_reader_and_keyboard(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[0].id
    c.open_article(aid)
    c.window.reader.toggle_save()
    assert storage.article(aid)["saved"] == 1
    assert c.window.reader.save_btn.text() == "Сохранено"


def test_ask_claude_about_article(gui):
    c, storage, _s, _ids = gui
    aid = c.feed.main[0].id
    c.open_article(aid)
    reader = c.window.reader
    reader.ask_line.setText("Кто проводит аудит?")
    reader.ask_line.returnPressed.emit()
    assert wait(lambda: "Ответ по статье: Кто проводит аудит?" in reader.answer.text())
    assert storage.one("SELECT 1 FROM events WHERE article_id=? AND kind='ask'", (aid,))


def test_survey_rules(gui):
    c, storage, settings, ids = gui
    settings.set("learning.survey_every", 1)
    aid = ids[0]
    assert c.should_survey(aid)
    c.react(aid, "like", True)
    assert not c.should_survey(aid)                         # уже есть явная оценка — не спрашиваем
    other = ids[1]
    c.survey(other, 5)
    assert not c.should_survey(other)                       # одна статья — один опрос
    settings.set("learning.survey_max_per_day", 1)
    assert not c.should_survey(ids[2])                      # не больше N в день


def test_explain_dialog(gui):
    c, _storage, _s, _ids = gui
    c.explain(c.feed.main[0].id, c.window)
    dialogs = [w for w in QAPP.topLevelWidgets() if w.objectName() == "Dialog" and w.isVisible()]
    assert dialogs and dialogs[0].windowTitle() == "Почему эта статья здесь"


# ---------------------------------------------------------------- поиск
def test_local_search_understands_word_forms(gui):
    c, _storage, _s, _ids = gui
    c.window.open_page("search")
    page = c.window.search_page
    page.box.setText("нейросеть")
    page.run_local()
    assert len(item_rows(page.list)) == 12
    page.box.setText("кваркглюон")
    page.run_local()
    assert not item_rows(page.list)
    assert "Claude поищет" in page.hint.text()


def test_web_search_adds_articles(gui, monkeypatch):
    c, storage, _s, _ids = gui
    with Site() as site:
        monkeypatch.setenv("FAKE_URLS", json.dumps([site.url("/en.html")]))
        c.window.open_page("search")
        page = c.window.search_page
        page.box.setText("модели")
        page.run_web()
        assert wait(lambda: page.web.isEnabled(), timeout=60)
    assert "Нашлось новых материалов: 1" in page.hint.text()
    assert storage.last_run("search")["query"] == "модели"


# ---------------------------------------------------------------- сбор и ошибки
def test_collect_now_refreshes_feed(gui, monkeypatch):
    c, storage, _s, _ids = gui
    with Site() as site:
        monkeypatch.setenv("FAKE_URLS", json.dumps([site.url("/en.html"), site.url("/ru.html")]))
        c.collect_now()
        assert c.window.feed_page.refresh_btn.text() == "Остановить"
        assert wait(lambda: c.collecting is None, timeout=60)
    assert storage.last_run("collect")["saved"] == 2
    assert wait(lambda: len(c.feed.all_items()) == 14)
    assert c.window.feed_page.refresh_btn.text() == "Собрать сейчас"


@pytest.mark.parametrize("mode,key,kind,button", [("auth", "claude", "error", "Войти в Claude"),
                                                  ("limit", "run", "info", None)])
def test_collect_errors_become_banners_with_fix(gui, monkeypatch, mode, key, kind, button):
    c, _storage, _s, _ids = gui
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    c.collect_now()
    assert wait(lambda: c.collecting is None, timeout=30)
    assert key in c.notices
    k, _text, label, fn = c.notices[key]
    assert k == kind and label == button
    if mode == "auth":
        assert not c.claude_status["ok"]


def test_missing_claude_shows_install_help(gui, monkeypatch):
    c, _storage, settings, _ids = gui
    settings.set("claude.command", "/nonexistent/claude")
    monkeypatch.setattr(appmod.claude_cli, "find_claude", lambda *_a, **_k: None)
    c.check_claude(quiet=True)
    assert wait(lambda: "claude" in c.notices)
    assert c.notices["claude"][2] == "Как установить"
    c.collect_now()
    dialogs = [w for w in QAPP.topLevelWidgets() if w.objectName() == "Dialog" and w.isVisible()]
    assert dialogs and dialogs[0].windowTitle() == "Нужен Claude Code"


def test_external_scheduled_run_is_noticed(gui):
    c, storage, _s, _ids = gui
    rid = storage.start_run("collect")
    c._poll()
    assert c._external_run and c.window.feed_page.progress.isVisible()
    storage.add_article({"url": "https://new.example/x", "title_ru": "Свежая по расписанию", "lang": "ru",
                         "topic": TOPICS[0], "summary_ru": ["факт"], "run_id": rid, "extract_status": "ok"})
    storage.finish_run(rid, "ok", saved=1)
    c._poll()
    assert not c._external_run and not c.window.feed_page.progress.isVisible()
    assert wait(lambda: any(it.article["title_ru"] == "Свежая по расписанию" for it in c.feed.all_items()))


def test_catch_up_after_pc_was_off(gui, monkeypatch):
    c, storage, _s, _ids = gui
    calls = []
    monkeypatch.setattr(c, "collect_now", lambda auto=False: calls.append(auto))
    c._maybe_catch_up()
    assert calls == [True]                                  # сборов ещё не было — догоняем
    rid = storage.start_run("collect")
    storage.finish_run(rid, "failed", error="limit: usage limit reached")
    calls.clear()
    c._maybe_catch_up()
    assert calls == []                                      # лимит — ждём, а не долбим


def test_stale_running_runs_are_healed(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    rid = storage.start_run("collect")
    storage.execute("UPDATE runs SET started=? WHERE id=?", (time.time() - 5 * 3600, rid))
    c = appmod.Controller(None, storage, Settings(tmp_path / "s.json"), services=False)
    c._heal_runs()
    assert storage.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "failed"


# ---------------------------------------------------------------- настройки и вид
def test_settings_page_and_theme_switch(gui):
    c, storage, settings, _ids = gui
    c.window.open_page("settings")
    assert "Последний сбор" in c.collect_status_text() or "Сборов ещё не было" in c.collect_status_text()
    settings.set("ui.theme", "light")
    c.apply_theme()
    assert c.window.colors["name"] == "light"
    settings.set("ui.text_size", "l")
    c.apply_theme()
    assert "14.2pt" in c.window.styleSheet()
    c.window.settings_page.rebuild()                        # перестройка не падает


def test_autostart_entry(gui):
    c, _storage, _s, _ids = gui
    c.set_autostart(True)
    text = appmod.AUTOSTART_FILE.read_text(encoding="utf-8")
    assert "gui --background" in text and "X-GNOME-Autostart-enabled=true" in text
    c.set_autostart(False)
    assert not appmod.AUTOSTART_FILE.exists()


def test_corrupted_db_shows_notice(tmp_path):
    db = tmp_path / "db.sqlite3"
    Storage(db).close()
    db.write_bytes(b"this is not a database" * 100)
    storage = Storage(db)
    c = appmod.Controller(QAPP, storage, Settings(tmp_path / "s.json"), services=False)
    c.settings.set("ui.welcome_done", True)
    c.start()
    assert "storage" in c.notices
    assert "восстановлена" in c.notices["storage"][1] or "новая" in c.notices["storage"][1]
    close_controller(c)
    storage.close()


def test_welcome_wizard_applies_choices(gui):
    c, storage, settings, _ids = gui
    from svodka.ui.welcome import Welcome
    wz = Welcome(c, c.window)
    wz.show()
    wz.go(2)
    t, seg, sw = wz.topic_controls[-1]                      # внешняя политика → «Реже» уже стоит
    assert seg.buttons[0].isChecked()
    seg.buttons[2].click()                                  # «Чаще»
    wz.new_topic.setText("Энергетика")
    wz._add_topic()
    wz.profile.setPlainText("Люблю системы и ИИ.")
    wz._next()
    assert storage.profile_text() == "Люблю системы и ИИ."
    topics = {x["name"]: x for x in storage.topics(enabled_only=False)}
    assert topics[t["name"]]["weight"] == 1.5 and "Энергетика" in topics
    wz.first.setChecked(False)
    settings.set("ui.welcome_done", False)
    wz._next()
    assert settings.get("ui.welcome_done") is True and not wz.isVisible()
    assert c.collecting is None


def test_coach_tips_shown_once(gui):
    c, _storage, settings, _ids = gui
    settings.set("ui.tips_seen", [])
    c.window.show_tips("feed")
    coach = c.window._coach
    assert coach is not None
    for _ in range(3):
        coach.next.click()
    assert c.window._coach is None and "feed" in settings.get("ui.tips_seen")


def test_personalization_off_still_builds_feed(gui):
    c, _storage, settings, _ids = gui
    settings.set("learning.personalization", False)
    c.refresh_feed()
    assert wait(lambda: c._ranker is not None and not c._ranker.personalization)
    assert len(c.feed.main) == 5


def test_double_click_area_opens_article(gui):
    c, _storage, _s, _ids = gui
    lst = c.window.feed_page.list
    c.window.show()
    QAPP.processEvents()
    opened = []
    lst.open_article.disconnect()
    lst.open_article.connect(opened.append)
    idx = next(i for i, r in enumerate(lst.model().rows) if r["kind"] == "item")
    rect = lst.visualRect(lst.model().index(idx))
    from PySide6.QtTest import QTest
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center() + QPoint(0, 8))
    assert opened == [lst.model().rows[idx]["item"].id]
