"""Приложение: окно + база + обучение + Claude.

Контроллер — единственное место, где интерфейс встречается с остальным:
- всё, что вы делаете (ответы, уверенность, время, оценки, пропуски), превращается в записи,
  на которых подстраиваются повторения, форматы и план;
- разговоры с Claude идут в фоновых потоках, текст приходит в окно потоком через сигнал Qt;
- активное время считается, только пока окно в фокусе и вы что-то делаете (как в «Сводке»);
- проблемы чинятся сами, а если не вышло — баннер на Главной или карточка в чате с кнопкой,
  которая их исправляет (как в Aqua): нет Claude, истёк вход, кончился лимит, нет сети.
"""
from __future__ import annotations

import gc
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import claude_cli, systemd, talk, tutor
from .config import (APP_ID, APP_NAME, BACKUP_DIR, CONFIG_DIR, DATA_DIR, DB_FILE, PROJECT_ROOT, STATE_DIR, Settings,
                     ensure_dirs, setup_logging)
from .learn import bandit, engine, metrics, planner
from .storage import Storage
from .util import DAY, day_key, day_start, now

log = logging.getLogger(__name__)

ACTIVE_GAP_S = 60                 # без мыши/клавиатуры дольше — время не считается
FLUSH_EVERY_S = 15                # активное время сохраняется в базу на случай сбоя
INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
AUTOSTART_FILE = CONFIG_DIR.parent / "autostart" / f"{APP_ID}.desktop"
SOCKET_NAME = f"{APP_ID}-{os.getuid()}"


class Bridge(QObject):
    """Перенос вызова из фонового потока в поток окна."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @staticmethod
    def _run(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — ошибка одного обработчика не должна ронять окно
            log.exception("обработчик в окне")


def main_thread_gc(app: QApplication) -> None:
    """Сборка мусора — только в потоке окна.

    Claude работает в фоновых потоках, и автоматическая сборка мусора Python может сработать прямо там.
    Если в мусоре окажется Qt-объект с таймером (закрытый диалог, старый контроллер), его деструктор
    выполнится не в том потоке: таймер останется в очереди окна, и на следующем срабатывании окно
    упадёт (segfault в QTimerInfoList::activateTimers — так падали тесты). Поэтому автоматическая
    сборка выключена, а раз в 10 секунд мусор собирается в потоке окна."""
    if getattr(app, "_nastavnik_gc", None) is not None:
        return
    gc.disable()
    timer = QTimer(app)
    timer.setInterval(10_000)
    timer.timeout.connect(gc.collect)
    timer.start()
    app._nastavnik_gc = timer


class ActivityFilter(QObject):
    """Мышь/клавиатура/колесо — признак, что вы здесь (активное время считается только тогда)."""

    KINDS = {QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel}

    def __init__(self, controller):
        super().__init__()
        self.c = controller

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802
        if event.type() in self.KINDS:
            self.c.last_activity = time.time()
        return False


def launcher_command() -> list[str]:
    local = Path.home() / ".local/bin/nastavnik"
    if local.exists() and os.access(local, os.X_OK):
        return [str(local)]
    return ["env", f"PYTHONPATH={PROJECT_ROOT}", sys.executable, "-m", "nastavnik"]


def desktop_entry(args: list[str], background: bool = False) -> str:
    exe = " ".join(shlex.quote(a) for a in launcher_command() + args)
    lines = ["[Desktop Entry]", "Type=Application", f"Name={APP_NAME}",
             "Comment=Учёба с Claude, которая подстраивается под вас", f"Exec={exe}", f"Icon={APP_ID}",
             "Terminal=false", "Categories=Education;", "StartupNotify=false" if background else "StartupNotify=true",
             f"StartupWMClass={APP_ID}"]
    if background:
        lines += ["X-GNOME-Autostart-enabled=true", "X-GNOME-Autostart-Delay=20"]
    return "\n".join(lines) + "\n"


def open_terminal(command: str) -> bool:
    """Открыть терминал с командой (вход в Claude, установка) — пароль и браузер видит сам человек."""
    shell = f"{command}; echo; read -r -p 'Готово. Нажмите Enter, чтобы закрыть окно…' _"
    variants = [("ptyxis", ["--", "bash", "-lc", shell]), ("gnome-terminal", ["--", "bash", "-lc", shell]),
                ("kgx", ["--", "bash", "-lc", shell]), ("konsole", ["-e", "bash", "-lc", shell]),
                ("xfce4-terminal", ["-x", "bash", "-lc", shell]), ("x-terminal-emulator", ["-e", "bash", "-lc", shell]),
                ("xterm", ["-e", "bash", "-lc", shell])]
    for name, args in variants:
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            subprocess.Popen([exe, *args], start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


class Controller(QObject):
    claude_changed = Signal()

    def __init__(self, app: QApplication | None, storage: Storage, settings: Settings, services: bool = True,
                 db_path: Path | None = None):
        super().__init__()
        self.app = app
        self.storage = storage
        self.settings = settings
        self.services = services            # False — в тестах: без таймеров systemd, трея и проверок Claude
        self.db_path = db_path or storage.path
        self.force_active = not services    # в тестах окно считается активным
        self.last_activity = time.time()
        self.claude_status: dict = {"ok": False, "installed": True, "message": "Проверяю вход в Claude…",
                                    "checked": False}
        self.bridge = Bridge()
        self.notices: dict = {}
        self.window = None
        self.tray = None
        self._cancel = threading.Event()
        self._threads: list[threading.Thread] = []
        self._login_watch = 0
        self._day = day_key(now())
        # активное время текущей сессии
        self.active_kind: str | None = None
        self.active_sid: int | None = None
        self.active_ms = 0
        self._last_flush = time.time()
        # учёба
        self.learn_sid: int | None = None
        self.learn_topic: int | None = None
        self.learn_phase = ""                 # chat | closing | rating | done
        self.learn_busy = False
        # разговор
        self.talk_sid: int | None = None
        self.talk_phase = ""                  # chat | closing | mood | memory | done
        self.talk_busy = False
        self._talk_mode_changed = False
        self._talk_mood_after: int | None = None

    # ================================================================ запуск
    def build_window(self):
        from .ui.main_window import MainWindow
        self.window = MainWindow(self)
        return self.window

    def start(self, background: bool = False) -> None:
        if self.window is None:
            self.build_window()
        if self.app is not None:
            self._activity = ActivityFilter(self)
            self.app.installEventFilter(self._activity)
            from .ui import textclick
            textclick.install(self.app)             # щелчок с дрожанием руки не выделяет букву (PRIMARY)
            main_thread_gc(self.app)
        if self.storage.problem:
            self.set_notice("storage", "error", self.storage.problem, "Понятно", lambda: self.clear_notice("storage"))
        if self.services:
            from .ui.tray import Tray
            self.tray = Tray(self) if Tray.available() else None
        if not (background and self.tray is not None):
            self.window.show()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        if self.services:
            self.check_claude(quiet=True)
            self.apply_timer()
            self.spawn(lambda: self.storage.backup(BACKUP_DIR))
            if self.settings.get("ui.welcome_done") and self.settings.get("ui.autostart", False):
                self.set_autostart(True)
        self.refresh_home()
        if not self.settings.get("ui.welcome_done"):
            QTimer.singleShot(350, self.window, self.show_welcome)    # не сработает, если окно уже закрыто

    def shutdown(self) -> None:
        self.on_close()
        self._cancel.set()
        self.detach()

    def detach(self) -> None:
        """Отцепиться от приложения: таймер и фильтр событий не должны пережить контроллер."""
        if getattr(self, "_tick_timer", None) is not None:
            self._tick_timer.stop()
        if self.app is not None and getattr(self, "_activity", None) is not None:
            self.app.removeEventFilter(self._activity)
            self._activity = None

    def on_close(self) -> None:
        """Окно закрывается: время сохранить, идущие сессии корректно завершить."""
        if self.learn_sid and self.learn_phase != "done":
            self.save_board_draft()
            tutor.finish_session(self.storage, self.learn_sid, None, self._end_active())
            self.learn_sid = None
        if self.talk_sid and self.talk_phase != "done":
            talk.finish(self.storage, self.settings, self.talk_sid, self._talk_mood_after, None, self._end_active())
            self.talk_sid = None
        self.end_review_session()

    # ================================================================ потоки
    def ui(self, fn, *args) -> None:
        self.bridge.call.emit(lambda: fn(*args))

    def spawn(self, fn, done=None, fail=None) -> threading.Thread:
        def work():
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, claude_cli.ClaudeError):
                    log.exception("фоновая задача")
                if fail is not None:
                    self.ui(fail, exc)
                return
            if done is not None:
                self.ui(done, res)
        t = threading.Thread(target=work, daemon=True)
        self._threads = [x for x in self._threads if x.is_alive()] + [t]
        t.start()
        return t

    # ================================================================ время
    def _window_active(self) -> bool:
        if self.force_active:
            return True
        return self.window is not None and self.window.isVisible() and self.window.isActiveWindow()

    def _begin_active(self, kind: str, sid: int) -> None:
        if self.active_kind:
            self._end_active()
        self.active_kind, self.active_sid, self.active_ms = kind, sid, 0
        self._last_flush = time.time()
        self.last_activity = time.time()

    def _end_active(self) -> int:
        ms = self.active_ms
        if self.active_sid:
            self.storage.update_session(self.active_sid, active_ms=ms)
        self.active_kind, self.active_sid, self.active_ms = None, None, 0
        return ms

    def _tick(self) -> None:
        if self.active_kind and self._window_active() and time.time() - self.last_activity < ACTIVE_GAP_S:
            self.active_ms += 1000
            if self.active_kind == "learn" and self.window is not None:
                self.window.session_view.set_minutes(self.active_ms // 60000)
            if time.time() - self._last_flush > FLUSH_EVERY_S:
                self._last_flush = time.time()
                self.storage.update_session(self.active_sid, active_ms=self.active_ms)
        today = day_key(now())
        if today != self._day:                    # новый день — новый план
            self._day = today
            if self.window is not None and self.window.current == "home" and not self.window.in_focus_mode():
                self.refresh_home()

    # ================================================================ данные для страниц
    def current_topic(self) -> dict | None:
        topics = self.storage.topics()
        if not topics:
            return None
        last = self.storage.last_checkpoint()
        if last:
            t = self.storage.topic(last["topic_id"])
            if t and not t["archived"]:
                return t
        recent = [s for s in self.storage.sessions(kind="learn") if s["topic_id"]]
        if recent:
            t = self.storage.topic(recent[-1]["topic_id"])
            if t and not t["archived"]:
                return t
        return topics[-1]

    def plan_for(self, topic_id: int | None) -> planner.Plan:
        return planner.plan_today(self.storage, self.settings, topic_id)

    def home_data(self) -> dict:
        ts = now()
        topics = self.storage.topics()
        topic = self.current_topic()
        mastered = studied = concepts = 0
        for t in topics:
            p = engine.topic_progress(self.storage, t["id"], ts)
            mastered += p["mastered"]
            studied += p["studied"]
            concepts += p["total"]
        due_all = len(self.storage.due_items(day_start(ts) + DAY - 1))
        return {
            "topics": topics, "topic": topic, "plan": self.plan_for(topic["id"]) if topic else None,
            "streak": metrics.streak(self.storage, ts, int(self.settings.get("learn.streak_freezes_per_week", 1))),
            "week_min": metrics.week_minutes(self.storage, ts),
            "week_goal": int(self.settings.get("learn.week_goal_minutes", 180)),
            "retention": engine.retention_all(self.storage, ts), "mastered": mastered, "studied": studied,
            "concepts": concepts,
            "due_all": due_all, "next_due": planner.next_due_text(self.storage, ts) if not due_all else "",
        }

    def progress_data(self) -> dict:
        ts = now()
        k = engine.memory_factor(self.storage)
        n = int(self.storage.meta_get(engine.FACTOR_N_KEY, "0") or 0)
        cards = self.storage.one("SELECT count(*) AS n FROM items WHERE reps>0 AND suspended=0")["n"]
        return {
            "week_min": metrics.week_minutes(self.storage, ts),
            "week_goal": int(self.settings.get("learn.week_goal_minutes", 180)),
            "streak": metrics.streak(self.storage, ts, int(self.settings.get("learn.streak_freezes_per_week", 1))),
            "best_hours": metrics.best_hours(self.storage, ts - 60 * DAY),
            "fatigue": metrics.fatigue_minutes(self.storage, ts - 60 * DAY),
            "retention": engine.retention_all(self.storage, ts), "memory_factor": k, "memory_n": n, "cards": cards,
            "formats": bandit.table(self.storage), "motivation": metrics.motivation(self.storage, ts),
            "topics": [{**t, "progress": engine.topic_progress(self.storage, t["id"], ts)}
                       for t in self.storage.topics()],
        }

    def talk_stats(self) -> dict:
        return metrics.talk_stats(self.storage)

    def next_due_text(self) -> str:
        return planner.next_due_text(self.storage)

    def refresh_home(self) -> None:
        if self.window is None:
            return
        self.window.home_page.render_notices(self.notices)
        if self.window.current == "home":
            self.window.home_page.refresh()
        st = metrics.streak(self.storage, now(), int(self.settings.get("learn.streak_freezes_per_week", 1)))
        self.window.set_status(f"Серия: {st['days']} дн." + (" · сегодня ✓" if st["studied_today"] else ""))

    # ================================================================ темы
    def new_topic_dialog(self) -> None:
        from .ui.learn_view import NewTopicDialog
        dlg = NewTopicDialog(self.window)
        if dlg.exec():
            self.create_topic(**dlg.values())

    def create_topic(self, title: str, goal: str = "", level: str = "beginner", notes: str = "") -> int:
        tid = self.storage.add_topic(title, goal, level, notes)
        self.window.open_topic(tid)
        self.build_map(tid)
        return tid

    def build_map(self, topic_id: int, replace: bool = False) -> None:
        if replace:
            self.storage.execute("DELETE FROM concepts WHERE topic_id=? AND status='new'", (topic_id,))
        self.window.learn_page.refresh()
        self.window.learn_page.set_map_status("Claude составляет карту темы… обычно 20–60 секунд.")

        def done(res):
            self.toast(f"Карта готова: {res['concepts']} понятий")
            if self.window.learn_page.topic_id == topic_id and not self.window.in_focus_mode():
                self.window.learn_page.refresh()
            self.refresh_home()

        def fail(exc):
            msg = exc.human() if isinstance(exc, claude_cli.ClaudeError) else f"Не получилось: {exc}"
            if isinstance(exc, claude_cli.ClaudeError):
                self._claude_failed(exc.kind, exc.message)
            self.window.learn_page.set_map_status(msg)
        self.spawn(lambda: tutor.build_map(self.storage, self.settings, topic_id, cancel=self._cancel), done, fail)

    def archive_topic(self, topic_id: int) -> None:
        self.storage.update_topic(topic_id, archived=1)
        self.window.learn_page.show_list()
        self.toast("Тема в архиве.", "Вернуть", lambda: (self.storage.update_topic(topic_id, archived=0),
                                                       self.window.learn_page.refresh()))

    # ================================================================ сессия учёбы
    def start_learning(self, topic_id: int) -> None:
        view = self.window.session_view
        if self.learn_sid and self.learn_phase in ("chat", "closing", "rating") and self.learn_topic == topic_id:
            self.window.open_session()                  # сессия уже идёт — просто вернуться в неё
            return
        if self.learn_sid and self.learn_phase != "done":
            tutor.finish_session(self.storage, self.learn_sid, None, self._end_active())
        if not self.storage.concepts(topic_id):
            self.window.open_topic(topic_id)
            self.toast("Сначала нужна карта темы.")
            return
        sid = tutor.start_session(self.storage, self.settings, topic_id)
        self.learn_sid, self.learn_topic, self.learn_phase = sid, topic_id, "chat"
        self._begin_active("learn", sid)
        view.begin(self.storage.topic(topic_id), self.storage.session(sid)["arms"])
        self.window.open_session()
        self._learn_turn(tutor.opening_prompt(self.storage, self.settings, sid), role="app")

    def learn_send(self, text: str, confidence: int | None = None) -> None:
        if not self.learn_sid or self.learn_busy or self.learn_phase != "chat":
            return
        self.window.session_view.chat.add_user(text, confidence)
        self._learn_turn(text, "user", confidence)

    def learn_send_board(self, data: dict, mermaid: str, png: bytes | None) -> None:
        """Схема с доски: Claude получает Mermaid (и картинку, если есть подписи от руки),
        комментарий и уверенность берутся из поля ответа."""
        if not self.learn_sid or self.learn_busy or self.learn_phase != "chat":
            return
        from . import sketch
        from .ui import board as board_ui
        from .ui import widgets as W
        view = self.window.session_view
        sid = self.learn_sid
        comment, confidence = view.input.take()
        board = sketch.Board.from_dict(data)
        last = self.storage.last_board(sid, sent=True)
        prev = sketch.Board.from_dict(last["data"]) if last else None
        text = sketch.board_prompt(board, comment, image=png is not None, prev=prev)
        self.storage.save_board(data, mermaid, png, session_id=sid, topic_id=self.learn_topic, sent=True)
        thumb = board_ui.render_image(board, max_w=920, pal=W.PALETTE, scale=1.0)
        caption = "Схема с доски: " + sketch.describe(board) + (" · с картинкой" if png else "")
        view.chat.add_board(thumb, caption, comment, confidence)
        view.suggest_board(False)
        self.storage.log_event("board_sent", value=len(board.shapes), meta=str(sid))
        self._learn_turn(text, "user", confidence, images=[png] if png else None)

    def save_board_draft(self) -> None:
        if not self.learn_sid or not self.window:
            return
        b = self.window.session_view.board.board
        if not b.is_empty():
            from . import sketch
            self.storage.save_board(b.to_dict(), sketch.to_mermaid(b), session_id=self.learn_sid,
                                    topic_id=self.learn_topic, sent=False)

    def _learn_turn(self, text: str, role: str, confidence: int | None = None, store: bool = True,
                    images: list[bytes] | None = None) -> None:
        view = self.window.session_view
        sid = self.learn_sid
        self.learn_busy = True
        view.set_busy(True)
        view.end_btn.setEnabled(False)
        view.chat.set_thinking("Claude думает…")
        view.chat.add_assistant()

        def piece(p: str) -> None:
            if sid == self.learn_sid:
                view.chat.set_thinking(None)
                view.chat.stream(p)

        def progress(msg: str) -> None:
            if sid == self.learn_sid and view.chat.current is not None and not view.chat._buffer:
                view.chat.set_thinking(msg + "…")

        def done(res: dict) -> None:
            if sid != self.learn_sid:
                return
            self.learn_busy = False
            view.chat.end_stream(res.get("text"))
            step = tutor.step_of(self.storage, sid)
            view.steps.set_step(step)
            arms = (self.storage.session(sid) or {}).get("arms") or {}
            view.after_answer(res.get("text") or "", step, arms.get("recall", ""))
            if res.get("rotated"):
                view.chat.add_note("Начат новый разговор с Claude — состояние темы передано, ничего не потеряно.")
            if self.learn_phase == "closing":
                self._learn_rating()
                return
            view.set_busy(False)
            view.end_btn.setEnabled(True)

        def fail(exc: Exception) -> None:
            if sid != self.learn_sid:
                return
            self.learn_busy = False
            view.chat.end_stream("")
            if self.learn_phase == "closing":           # итог важнее: даже без Claude сессию можно завершить
                view.chat.add_note("Claude не ответил на завершение — сессия всё равно сохранится.")
                self._learn_rating()
                return
            kind = getattr(exc, "kind", "failed")
            msg = exc.human() if isinstance(exc, claude_cli.ClaudeError) else f"Что-то пошло не так: {exc}"
            fix_label, fix = self._fix_for(kind)
            if isinstance(exc, claude_cli.ClaudeError):
                self._claude_failed(kind, exc.message)

            def retry() -> None:
                card.hide()
                self._learn_turn(text, role, confidence, store=False, images=images)
            card = view.error_card(msg, fix_label, fix, retry)
            view.chat.add_widget(card)
            view.set_busy(False)
            view.end_btn.setEnabled(True)

        db = self.db_path
        self.spawn(lambda: tutor.send(self.storage, self.settings, sid, text, role, confidence,
                                      on_text=lambda p: self.ui(piece, p), on_progress=lambda m: self.ui(progress, m),
                                      cancel=self._cancel, db_path=db, store=store, images=images), done, fail)

    def learn_end(self) -> None:
        if not self.learn_sid or self.learn_busy:
            if self.learn_busy:
                self.toast("Дождитесь ответа Claude — потом можно заканчивать.")
            return
        if self.learn_phase != "chat":
            return
        has_answers = bool([m for m in self.storage.messages(self.learn_sid) if m["role"] == "user"])
        self.learn_phase = "closing"
        if not has_answers:                          # ничего не успели — без подарка и петли
            self._learn_rating()
            return
        self._learn_turn(tutor.CLOSING_PROMPT, "app")

    def _learn_rating(self) -> None:
        view = self.window.session_view
        self.save_board_draft()
        view.close_board()
        self.learn_phase = "rating"
        view.set_busy(True)
        view.input.send.setText("Сессия завершена")
        view.end_btn.setEnabled(False)
        view.chat.add_widget(view.finished_card(self.learn_finish))

    def learn_finish(self, liking: int | None) -> None:
        if not self.learn_sid or self.learn_phase == "done":
            return
        sid = self.learn_sid
        summary = tutor.finish_session(self.storage, sid, liking, self._end_active())
        self.learn_phase = "done"
        view = self.window.session_view
        view.chat.add_widget(view.summary_card(summary, self.learn_close))
        self.refresh_home()

    def learn_close(self) -> None:
        self.save_board_draft()
        topic = self.learn_topic
        if self.learn_sid and self.learn_phase != "done":
            tutor.finish_session(self.storage, self.learn_sid, None, self._end_active())
        self.learn_sid, self.learn_phase = None, ""
        if topic:
            self.window.open_topic(topic)
        else:
            self.window.open_page("home")

    def learn_leave(self) -> None:
        """«‹ Тема» посреди сессии: закончить как обычно или выйти без итога."""
        if not self.learn_sid or self.learn_phase == "done":
            self.learn_close()
            return
        if self.learn_phase == "rating":
            self.learn_finish(None)
            self.learn_close()
            return
        from .ui.dialogs import message_dialog
        message_dialog(self.window, "Закончить сессию?",
                       "Если закончить как обычно, Claude подведёт итог, сделает карточки для повторения и оставит "
                       "вопрос на следующий раз.",
                       buttons=[("Выйти без итога", self.learn_close, False),
                                ("Закончить как обычно", self.learn_end, True)],
                       close_label="Продолжить")

    # ================================================================ повторение
    def review_queue(self, topic_id: int | None = None) -> list[dict]:
        ts = now()
        items = self.storage.due_items(day_start(ts) + DAY - 1, topic_id)
        from .learn import fsrs
        k = engine.memory_factor(self.storage)
        items.sort(key=lambda i: (fsrs.current_r(i, ts, k) if fsrs.current_r(i, ts, k) is not None else 1.0))
        return items

    def review_ahead_queue(self, topic_id: int | None = None) -> list[dict]:
        """Карточки, у которых срок ещё не подошёл, — чтобы повторить самому: сначала те, что ближе к сроку."""
        end = day_start(now()) + DAY - 1
        items = [i for i in self.storage.items(topic_id=topic_id) if not i["suspended"] and (i["due"] or 0) > end]
        items.sort(key=lambda i: i["due"] or 0)
        return items

    def begin_review_session(self, topic_id: int | None) -> int:
        ts = now()
        trigger = metrics.trigger_for(self.storage, ts)
        sid = self.storage.start_session("review", topic_id, started=ts, trigger_ts=trigger,
                                         self_started=0 if trigger else 1)
        self._begin_active("review", sid)
        return sid

    def review_grade(self, item_id: int, grade: int, latency_ms: int, session_id: int | None,
                     ahead: bool = False, note: str = "") -> dict:
        return engine.record_review(self.storage, self.settings, item_id, grade, latency_ms=latency_ms,
                                    session_id=session_id, ahead=ahead, note=note)

    def review_check(self, item: dict, answer: str, seconds: float, png: bytes | None, done, fail) -> None:
        """Claude проверяет ответ на карточку в фоне; done({'grade', 'explanation'}) / fail(ClaudeError)."""
        from . import review_check

        def failed(exc: Exception) -> None:
            if isinstance(exc, claude_cli.ClaudeError) and exc.kind in ("auth", "not_installed", "limit"):
                self._claude_failed(exc.kind, exc.message)
            fail(exc)
        self.spawn(lambda: review_check.check_answer(self.settings, item, answer, seconds, png, cancel=self._cancel),
                   done, failed)

    def end_review_session(self) -> None:
        if self.active_kind == "review":
            sid = self.active_sid
            ms = self._end_active()
            self.storage.update_session(sid, ended=now(), active_ms=ms)
            if self.window is not None:
                self.window.review_page.session_id = None
            self.refresh_home()

    # ================================================================ разговор
    def talk_start(self, mode: str, mood: int | None, feeling: str = "") -> None:
        if self.talk_sid and self.talk_phase not in ("done", ""):
            self.window.open_talk_session()
            return
        sid = talk.start(self.storage, self.settings, mode, mood, feeling)
        self.talk_sid, self.talk_phase = sid, "chat"
        self._talk_mode_changed = False
        self._talk_mood_after = None
        self._begin_active("talk", sid)
        self.window.talk_view.begin(mode)
        self.window.open_talk_session()

    def talk_set_mode(self, mode: str) -> None:
        if not self.talk_sid:
            return
        talk.set_mode(self.storage, self.talk_sid, mode)
        self._talk_mode_changed = True
        self.window.talk_view.chat.add_note(f"Режим: {talk.MODES.get(mode, mode)}")

    def talk_send(self, text: str) -> None:
        if not self.talk_sid or self.talk_busy or self.talk_phase != "chat":
            return
        self.window.talk_view.chat.add_user(text)
        changed, self._talk_mode_changed = self._talk_mode_changed, False
        self._talk_turn(lambda on_text: talk.send(self.storage, self.settings, self.talk_sid, text, on_text=on_text,
                                                  cancel=self._cancel, mode_changed=changed),
                        retry=lambda: self.talk_send_again(text))

    def talk_send_again(self, text: str) -> None:
        self._talk_turn(lambda on_text: talk.send(self.storage, self.settings, self.talk_sid, text, on_text=on_text,
                                                  cancel=self._cancel), retry=lambda: self.talk_send_again(text))

    def _talk_turn(self, call, retry=None, then=None) -> None:
        view = self.window.talk_view
        sid = self.talk_sid
        self.talk_busy = True
        view.input.set_busy(True)
        view.chat.set_thinking("Claude слушает…")
        view.chat.add_assistant()

        def piece(p: str) -> None:
            if sid == self.talk_sid:
                view.chat.set_thinking(None)
                view.chat.stream(p)

        def done(res: dict) -> None:
            if sid != self.talk_sid:
                return
            self.talk_busy = False
            view.chat.end_stream(res.get("text"))
            view.input.set_busy(False)
            if then:
                then()

        def fail(exc: Exception) -> None:
            if sid != self.talk_sid:
                return
            self.talk_busy = False
            view.chat.end_stream("")
            if then:                                  # при завершении сбой не мешает закончить разговор
                then()
                return
            kind = getattr(exc, "kind", "failed")
            msg = exc.human() if isinstance(exc, claude_cli.ClaudeError) else f"Что-то пошло не так: {exc}"
            fix_label, fix = self._fix_for(kind)
            from .ui.learn_view import error_card
            card = error_card(msg, fix_label, fix, (lambda: (card.hide(), retry())) if retry else None)
            view.chat.add_widget(card)
            view.input.set_busy(False)
        self.spawn(lambda: call(lambda p: self.ui(piece, p)), done, fail)

    def talk_end(self) -> None:
        if not self.talk_sid:
            self.window.open_page("talk")
            return
        if self.talk_busy:
            self.toast("Дождитесь ответа — потом можно завершать.")
            return
        if self.talk_phase != "chat":
            return
        view = self.window.talk_view
        view.end_btn.setEnabled(False)
        view.input.set_busy(True)
        self.talk_phase = "closing"
        if not self.storage.session(self.talk_sid)["claude_session"]:   # ничего не сказали — просто выйти
            self._talk_finish(None, None)
            self.talk_close()
            return
        self._talk_turn(lambda on_text: talk.closing(self.storage, self.settings, self.talk_sid, on_text=on_text,
                                                     cancel=self._cancel), then=self._talk_mood)

    def _talk_mood(self) -> None:
        view = self.window.talk_view
        self.talk_phase = "mood"
        view.input.set_busy(True)
        view.input.send.setText("Разговор завершён")
        view.chat.add_widget(view.mood_card(self._talk_mood_done))

    def _talk_mood_done(self, mood_after: int) -> None:
        view = self.window.talk_view
        self._talk_mood_after = mood_after
        memory = self.settings.get("talk.memory", "ask")
        if memory == "never":
            self._talk_finish(mood_after, None)
            return
        self.talk_phase = "memory"
        view.chat.set_thinking("Готовлю короткую сводку…")
        sid = self.talk_sid

        def done(note):
            if sid != self.talk_sid:
                return
            view.chat.set_thinking(None)
            if not note:
                self._talk_finish(mood_after, None)
            elif memory == "always":
                self._talk_finish(mood_after, note)
            else:
                view.chat.add_widget(view.memory_card(note, lambda n: self._talk_finish(mood_after, n),
                                                      lambda: self._talk_finish(mood_after, None)))

        def fail(_exc):
            view.chat.set_thinking(None)
            self._talk_finish(mood_after, None)
        self.spawn(lambda: talk.summarize(self.storage, self.settings, sid, cancel=self._cancel), done, fail)

    def _talk_finish(self, mood_after: int | None, note: dict | None) -> None:
        if not self.talk_sid or self.talk_phase == "done":
            return
        s = self.storage.session(self.talk_sid)
        res = talk.finish(self.storage, self.settings, self.talk_sid, mood_after, note, self._end_active())
        self.talk_phase = "done"
        view = self.window.talk_view
        parts = []
        if res["delta"] is not None and s["mood_before"] is not None:
            parts.append(f"Было {s['mood_before']} → стало {mood_after}.")
        if note:
            parts.append("Запомнено.")
        if res["transcript_deleted"]:
            parts.append("Переписка удалена.")
        view.chat.add_widget(view.done_card(" ".join(parts) or "Разговор завершён.", self.talk_close))

    def talk_close(self) -> None:
        if self.talk_sid and self.talk_phase != "done":
            self._talk_finish(self._talk_mood_after, None)
        self.talk_sid, self.talk_phase = None, ""
        self.window.open_page("talk")

    # ================================================================ Claude: вход и ошибки
    def _fix_for(self, kind: str):
        if kind == "auth":
            return "Войти в Claude", self.login_claude
        if kind == "not_installed":
            return "Как установить", self.install_claude
        return None, None

    def check_claude(self, quiet: bool = False) -> None:
        cmd = self.settings.get("claude.command", "claude")

        def done(st):
            st = dict(st, checked=True)
            self.claude_status = st
            if st.get("ok"):
                self.clear_notice("claude")
                if not quiet:
                    self.toast("Claude: вход выполнен, работает по подписке")
            elif not st.get("installed", True):
                self._claude_failed("not_installed", st.get("message", ""))
            else:
                self._claude_failed("auth", st.get("message", ""))
            self.claude_changed.emit()
            if self.window is not None and self.window.current == "settings" and not self.window.in_focus_mode():
                self.window.settings_page.rebuild()
        self.spawn(lambda: claude_cli.auth_status(cmd), done)

    def _claude_failed(self, kind: str, message: str) -> None:
        """Понятная причина и кнопка, которая её исправляет."""
        if kind == "not_installed":
            self.claude_status = {"ok": False, "installed": False, "message": message, "checked": True}
            self.set_notice("claude", "error", "Claude Code не установлен — без него сессии не начнутся.",
                            "Как установить", self.install_claude)
        elif kind == "auth":
            self.claude_status = {"ok": False, "installed": True, "message": message, "checked": True}
            self.set_notice("claude", "error", "Вход в Claude истёк или не выполнен.", "Войти в Claude",
                            self.login_claude)
        elif kind == "limit":
            self.set_notice("limit", "info", "Лимит подписки Claude на время исчерпан. Повторение карточек работает "
                            "и без Claude.", "Понятно", lambda: self.clear_notice("limit"))
        elif kind == "network":
            self.set_notice("network", "info", "Нет связи с Claude. Повторение карточек работает и без сети.",
                            "Понятно", lambda: self.clear_notice("network"))
        self.claude_changed.emit()

    def login_claude(self) -> None:
        exe = claude_cli.find_claude(self.settings.get("claude.command", "claude"))
        if not exe:
            self.install_claude()
            return
        if open_terminal(f"{shlex.quote(exe)} auth login"):
            self.toast("В терминале откроется вход в Claude — войдите тем же аккаунтом, что и на claude.ai.", ms=8000)
            self._watch_login()
        else:
            from .ui.dialogs import message_dialog
            message_dialog(self.window, "Вход в Claude", "Откройте терминал и выполните команду:",
                           code=f"{exe} auth login", buttons=[("Проверить вход", self.check_claude, True)])

    def _watch_login(self) -> None:
        """Пока человек входит в терминале — проверяем раз в 5 секунд (до 5 минут)."""
        self._login_watch = 60

        def tick():
            if self._login_watch <= 0 or self.claude_status.get("ok"):
                return
            self._login_watch -= 1
            self.check_claude(quiet=False)
            QTimer.singleShot(5000, tick)
        QTimer.singleShot(5000, tick)

    def install_claude(self) -> None:
        from .ui.dialogs import message_dialog

        def install():
            if open_terminal(INSTALL_CMD + " && ~/.local/bin/claude auth login"):
                self.toast("Установка идёт в терминале. После входа «Наставник» заметит это сам.", ms=8000)
                self._watch_login()
        message_dialog(self.window, "Нужен Claude Code",
                       "Наставник работает через Claude Code по вашей подписке — ключ API не нужен. "
                       "Установка занимает минуту:", code=INSTALL_CMD + "\nclaude auth login",
                       buttons=[("Установить в терминале", install, True),
                                ("Проверить снова", lambda: self.check_claude(), False)])

    # ================================================================ настройки и служебное
    def apply_timer(self) -> None:
        if not self.services:
            return
        t, on = self.settings.get("reminder.time", "19:07"), bool(self.settings.get("reminder.enabled", True))

        def done(res):
            ok, msg = res
            if not ok:
                self.set_notice("timer", "info", f"Напоминание не поставилось ({msg}). Пока окно открыто, "
                                "план виден на Главной.", "Повторить", lambda: (self.clear_notice("timer"),
                                                                              self.apply_timer()))
            else:
                self.clear_notice("timer")
        self.spawn(lambda: systemd.ensure(t, on), done)

    def set_autostart(self, on: bool) -> None:
        try:
            if on:
                AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
                AUTOSTART_FILE.write_text(desktop_entry(["gui", "--background"], background=True), encoding="utf-8")
            elif AUTOSTART_FILE.exists():
                AUTOSTART_FILE.unlink()
        except OSError as exc:
            log.warning("автозапуск: %s", exc)

    def reset_experiments(self) -> None:
        from .ui.dialogs import message_dialog
        message_dialog(self.window, "Сбросить эксперименты?",
                       "Всё, что система узнала о форматах подачи, крючках и подарках, будет забыто. Карточки, "
                       "прогресс и время останутся.",
                       buttons=[("Сбросить", lambda: (self.storage.reset_arms(), self.toast("Эксперименты сброшены")),
                                 True)], close_label="Отмена")

    def export(self, path: str) -> None:
        Path(path).write_text(tutor.export_json(self.storage), encoding="utf-8")
        self.toast(f"Сохранено: {path}")

    def data_dir(self) -> Path:
        return DATA_DIR

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR)))

    def set_notice(self, key: str, kind: str, text: str, label: str | None, fn) -> None:
        self.notices[key] = (kind, text, label, fn)
        if self.window is not None:
            self.window.home_page.render_notices(self.notices)

    def clear_notice(self, key: str) -> None:
        if self.notices.pop(key, None) is not None and self.window is not None:
            self.window.home_page.render_notices(self.notices)

    def toast(self, text: str, action: str | None = None, fn=None, ms: int = 5000) -> None:
        if self.window is not None:
            self.window.toast(text, action, fn, ms)

    def show_welcome(self) -> None:
        from .ui.welcome import Welcome
        Welcome(self, self.window).exec()
        self.refresh_home()


# ==================================================================== точка входа
def _other_instance() -> bool:
    """Второй запуск просто показывает уже открытое окно."""
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _listen(controller: Controller) -> QLocalServer:
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)

    def incoming():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), controller.window.bring_to_front()))
    server.newConnection.connect(incoming)
    return server


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    background = "--background" in argv
    setup_logging()
    ensure_dirs()
    QApplication.setApplicationName(APP_ID)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    if _other_instance():
        return 0
    from .ui.look import app_icon, load_fonts
    from .ui.theme import apply_app_font
    load_fonts()
    apply_app_font(app)
    app.setWindowIcon(app_icon())
    settings = Settings()
    storage = Storage(DB_FILE)
    controller = Controller(app, storage, settings)
    server = _listen(controller)

    def excepthook(exc_type, exc, tb):
        log.error("Необработанная ошибка:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            controller.toast("Что-то пошло не так — записал в журнал, работа продолжается.")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = excepthook
    try:
        (STATE_DIR / "gui.pid").write_text(str(os.getpid()))
    except OSError:
        pass
    controller.start(background=background)
    app.aboutToQuit.connect(controller.shutdown)
    code = app.exec()
    server.close()
    storage.close()
    return code
