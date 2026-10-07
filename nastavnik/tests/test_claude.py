"""Claude-слой с поддельным claude: карта темы, сессия через настоящий MCP-сервер, новый разговор
после лимита контекста, самопочинка потерянного разговора, разговор-«выговориться», напоминание."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from nastavnik import claude_cli, systemd, talk, tutor
from nastavnik.config import Settings
from nastavnik.learn import engine
from nastavnik.mcp_server import Service
from nastavnik.storage import Storage
from tests.conftest import FAKE_BIN, FAKE_CLAUDE, TMP_HOME

os.environ["PATH"] = os.pathsep.join([str(FAKE_BIN), str(Path(sys.executable).parent), os.environ.get("PATH", "")])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.delenv("FAKE_TOKENS", raising=False)
    st = Storage(tmp_path / "db.sqlite3")
    settings = Settings(tmp_path / "settings.json")
    settings.set("claude.command", str(FAKE_CLAUDE))
    settings.set("profile.interests", "игры, компьютерные сети, свои проекты")
    yield st, settings, tmp_path / "db.sqlite3"
    st.close()


def new_topic(st, settings):
    tid = st.add_topic("Компьютерные сети", "понимать, как ходит трафик", "beginner")
    res = tutor.build_map(st, settings, tid)
    assert res["concepts"] == 5
    return tid


def test_topic_map_from_claude(env):
    st, settings, _ = env
    tid = new_topic(st, settings)
    concepts = st.concepts(tid)
    assert [c["slug"] for c in concepts] == ["c0", "c1", "c2", "c3", "c4"]
    assert concepts[1]["prereqs"] == ["c0"] and concepts[3]["kind"] == "procedure"
    assert "Первый вопрос" in st.topic(tid)["notes"]
    assert "Идея для первого крючка" in tutor.state_block(st, settings, tid)


def test_learning_session_through_real_mcp(env):
    st, settings, db = env
    tid = new_topic(st, settings)
    sid = tutor.start_session(st, settings, tid)
    s = st.session(sid)
    assert set(s["arms"]) >= {"order", "present", "recall", "hook", "gift"} and s["plan"]["concept"] == "c0"
    pieces = []
    r1 = tutor.send(st, settings, sid, tutor.opening_prompt(st, settings, sid), role="app",
                    on_text=pieces.append, db_path=db)
    assert "Крючок" in r1["text"] and "".join(pieces).strip() == r1["text"]
    assert "(вижу состояние темы)" in r1["text"]           # первый ход нового разговора получает состояние
    assert tutor.step_of(st, sid) == "hook"
    time.sleep(0.05)
    r2 = tutor.send(st, settings, sid, "Наверное, пакеты идут разными путями", confidence=3, db_path=db)
    assert r2["text"].startswith("Верно") and "(вижу состояние темы)" not in r2["text"]   # продолжение разговора
    tries = st.attempts(session_id=sid)
    assert len(tries) == 1 and tries[0]["correct"] == 1 and tries[0]["confidence"] == 3
    assert tries[0]["latency_ms"] >= 40                        # время ответа считает приложение, не Claude
    tutor.send(st, settings, sid, "не знаю", db_path=db)
    assert st.attempts(session_id=sid)[-1]["correct"] == 0
    tutor.send(st, settings, sid, tutor.CLOSING_PROMPT, role="app", db_path=db)
    assert tutor.step_of(st, sid) == "loop"
    c0 = st.concept_by_slug(tid, "c0")
    assert c0["intro_session"] == sid and c0["status"] == "learning"
    items = st.items(concept_id=c0["id"])
    assert len(items) == 2 and {i["kind"] for i in items} == {"card", "schema"}
    assert all(i["due"] - time.time() < 1.2 * 86400 for i in items)   # практика 1 из 2 — повтор уже завтра
    summary = tutor.finish_session(st, sid, liking=5, active_ms=17 * 60_000)
    assert summary["open_loop"] == "Почему TCP тормозит при потерях?" and summary["cards"] == 2
    assert summary["concepts"] == ["Основы"] and summary["now_can"]
    arms = st.session(sid)["arms"]
    assert st.arm_stats("hook", "all")[arms["hook"]]["alpha"] == 2.0     # оценка 5 → награда крючку
    assert st.topic(tid)["claude_session"]


def test_topic_conversation_is_not_glued_to_parent_claude_session(env, monkeypatch):
    """Приложение запущено из терминала внутри Claude Code: у темы всё равно свой разговор."""
    st, settings, db = env
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    tid = new_topic(st, settings)
    sid = tutor.start_session(st, settings, tid)
    tutor.send(st, settings, sid, tutor.opening_prompt(st, settings, sid), role="app", db_path=db)
    assert st.topic(tid)["claude_session"] not in ("", "parent-session")


def test_new_conversation_after_context_limit(env, monkeypatch):
    st, settings, db = env
    tid = new_topic(st, settings)
    settings.set("learn.context_tokens", 8000)
    monkeypatch.setenv("FAKE_TOKENS", "9000")
    sid = tutor.start_session(st, settings, tid)
    tutor.send(st, settings, sid, tutor.opening_prompt(st, settings, sid), role="app", db_path=db)
    first = st.topic(tid)["claude_session"]
    assert st.topic(tid)["claude_tokens"] == 9000
    r = tutor.send(st, settings, sid, "Пакеты", db_path=db)
    assert r["rotated"] and "(вижу состояние темы)" in r["text"]
    assert st.topic(tid)["claude_session"] != first
    assert st.events("context_rotated")


def test_lost_conversation_heals_itself(env, monkeypatch):
    st, settings, db = env
    tid = new_topic(st, settings)
    st.update_topic(tid, claude_session="old-session-from-another-pc", claude_tokens=100)
    sid = tutor.start_session(st, settings, tid)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "nosession")
    r = tutor.send(st, settings, sid, tutor.opening_prompt(st, settings, sid), role="app", db_path=db)
    assert r["rotated"] and "Крючок" in r["text"] and st.events("context_lost")


@pytest.mark.parametrize("mode,kind", [("auth", "auth"), ("limit", "limit"), ("broken", "failed")])
def test_claude_errors_are_human(env, monkeypatch, mode, kind):
    st, settings, db = env
    tid = st.add_topic("Тема")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(claude_cli.ClaudeError) as exc:
        tutor.build_map(st, settings, tid)
    assert exc.value.kind == kind and exc.value.human()


def test_mcp_service_checks_what_claude_writes(env):
    st, settings, _ = env
    tid = new_topic(st, settings)
    sid = tutor.start_session(st, settings, tid)
    svc = Service(st, settings, sid, tid)
    assert "ошибка" in svc.mark_concept("нет-такого")
    assert "ошибка" in svc.add_items("нет-такого", [{"prompt": "q"}])
    assert "ошибка" in svc.set_step("party")
    svc.record_attempt("c1", "whatever", True, 9)
    a = st.attempts(session_id=sid)[-1]
    assert a["phase"] == "practice" and a["grade"] == 4
    assert st.concept_by_slug(tid, "c1")["status"] == "learning"     # ответ по понятию = понятие начато
    for _ in range(2):
        svc.record_attempt("c1", "practice", True, 3)
    assert "усложнить" in svc.record_attempt("c1", "practice", True, 3)
    assert "Основы" in svc.get_state()


def test_talk_session_remembers_only_what_was_allowed(env):
    st, settings, _ = env
    st.add_talk_note(None, "Говорили о сне", ["сон"], "назвать чувство")
    sid = talk.start(st, settings, "listen", 3, "усталость")
    r = talk.send(st, settings, sid, "Всё валится из рук, я просто ленивый")
    assert "усталость" in r["text"] and "(помню прошлые разговоры)" in r["text"]
    cs = st.session(sid)["claude_session"]
    transcript = list((TMP_HOME / ".claude" / "projects").glob(f"*/{cs}.jsonl"))
    assert transcript                                     # Claude Code ведёт журнал разговора…
    assert not st.messages(sid)                           # …а приложение переписку не хранит
    talk.closing(st, settings, sid)
    note = talk.summarize(st, settings, sid)
    assert note["themes"] == ["работа", "усталость"]
    res = talk.finish(st, settings, sid, 6, note)
    assert res["delta"] == 3 and res["transcript_deleted"]
    assert not list((TMP_HOME / ".claude" / "projects").glob(f"*/{cs}.jsonl"))   # журнал удалён
    assert st.talk_notes()[0]["summary"].startswith("Ты рассказал")


def test_talk_without_memory_and_with_kept_transcript(env):
    st, settings, _ = env
    settings.set("talk.memory", "never")
    settings.set("talk.keep_transcripts", True)
    st.add_talk_note(None, "старое", ["x"])
    sid = talk.start(st, settings, "act", 5)
    r = talk.send(st, settings, sid, "Что делать с завалом задач?")
    assert "(помню" not in r["text"]
    assert talk.summarize(st, settings, sid) is None
    talk.finish(st, settings, sid, 6, None)
    assert len(st.messages(sid)) == 2                     # переписку попросили хранить — хранится


def test_reminder_only_when_not_studied_today(env, tmp_path, monkeypatch):
    st, settings, _ = env
    log = tmp_path / "notify.log"
    monkeypatch.setenv("FAKE_NOTIFY_LOG", str(log))
    tid = new_topic(st, settings)
    st.add_checkpoint(tid, None, "пакеты", "", "Почему TCP тормозит при потерях?")
    res = systemd.remind(st, settings)
    assert res["sent"] and "TCP" in log.read_text() and len(st.events("reminder")) == 1
    s = st.start_session("review", None)
    st.update_session(s, ended=time.time(), active_ms=5 * 60_000)
    res2 = systemd.remind(st, settings)
    assert not res2["sent"] and len(st.events("reminder")) == 1
    assert "nastavnik remind --scheduled" in systemd.service_text()
    assert "OnCalendar=*-*-* 19:07:00" in systemd.timer_text("19:07") and "Persistent=true" in systemd.timer_text("19:07")


def test_engine_review_after_session_items(env):
    st, settings, db = env
    tid = new_topic(st, settings)
    sid = tutor.start_session(st, settings, tid)
    tutor.send(st, settings, sid, tutor.opening_prompt(st, settings, sid), role="app", db_path=db)
    tutor.send(st, settings, sid, "пакеты", db_path=db)
    tutor.send(st, settings, sid, tutor.CLOSING_PROMPT, role="app", db_path=db)
    item = st.items(topic_id=tid)[0]
    res = engine.record_review(st, settings, item["id"], 3, ts=time.time() + 3 * 86400)
    assert res["phase"] == "delayed"
    arms = st.session(sid)["arms"]
    assert st.arm_stats("order", arms["kind"])[arms["order"]]["n"] == 1
