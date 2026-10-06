"""Приложение: окно + хранилище + обучение + Claude.

Контроллер — единственное место, где интерфейс встречается с остальным:
- все действия читателя (показы, открытия, время чтения, реакции, опросы) превращаются в
  записи журнала, на которых учится лента (rank/);
- долгие дела (сбор, поиск, перевод, вопрос Claude, импорт, разбор недели) идут в фоновых
  потоках, а результат возвращается в окно через сигнал Qt;
- проблемы чинятся сами, а если не вышло — баннер на Ленте с кнопкой, которая их исправляет
  (как в Aqua): нет Claude, истёк вход, кончился лимит, нет сети, не работает расписание.
"""
from __future__ import annotations

import json
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
from PySide6.QtWidgets import QApplication, QMenu

from . import claude_cli, collect, systemd, weekly
from .config import (APP_ID, APP_NAME, CONFIG_DIR, DB_FILE, PROJECT_ROOT, PROMPTS_DIR, RUNTIME_DIR, STATE_DIR,
                     Settings, ensure_dirs, setup_logging)
from .rank.ranker import Ranker
from .storage import Storage

log = logging.getLogger(__name__)

EXPLICIT = ("like", "superlike", "dislike", "known", "shallow", "clickbait")
TOGGLE_KINDS = EXPLICIT + ("save", "follow")
HIDE_REASONS = [("dislike", "Не моя тема"), ("known", "Уже знал"), ("shallow", "Поверхностно"),
                ("clickbait", "Заголовок обманул")]
FEED_STALE_S = 20 * 60          # после чтения лента пересобирается, если старше 20 минут…
FEED_SIGNALS_REBUILD = 3        # …или накопилось 3 новых действия
POLL_MS = 30_000
RUNNING_STALE_S = 45 * 60
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


class ActivityFilter(QObject):
    """Мышь/клавиатура/колесо — признак, что читатель здесь (время чтения считается только тогда)."""

    KINDS = {QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel}

    def __init__(self, controller):
        super().__init__()
        self.c = controller

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802
        if event.type() in self.KINDS:
            self.c.last_activity = time.time()
        return False


def launcher_command() -> list[str]:
    """Как запускать приложение снаружи (ярлык, автозапуск)."""
    local = Path.home() / ".local/bin/svodka"
    if local.exists() and os.access(local, os.X_OK):
        return [str(local)]
    return ["env", f"PYTHONPATH={PROJECT_ROOT}", sys.executable, "-m", "svodka"]


def desktop_entry(args: list[str], background: bool = False) -> str:
    exe = " ".join(shlex.quote(a) for a in launcher_command() + args)
    lines = ["[Desktop Entry]", "Type=Application", f"Name={APP_NAME}",
             "Comment=Личная лента новостей с переводом и рекомендациями", f"Exec={exe}",
             f"Icon={APP_ID}", "Terminal=false", "Categories=Network;News;", "StartupNotify=false" if background
             else "StartupNotify=true", f"StartupWMClass={APP_ID}"]
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


def when_text(ts: float) -> str:
    t = time.localtime(ts)
    today = time.localtime()
    if t.tm_yday == today.tm_yday and t.tm_year == today.tm_year:
        return f"сегодня в {time.strftime('%H:%M', t)}"
    if time.time() - ts < 2 * 86400 and (today.tm_yday - t.tm_yday) % 366 == 1:
        return f"вчера в {time.strftime('%H:%M', t)}"
    return time.strftime("%d.%m в %H:%M", t)


class Controller(QObject):
    claude_changed = Signal()

    def __init__(self, app: QApplication | None, storage: Storage, settings: Settings, services: bool = True):
        super().__init__()
        self.app = app
        self.storage = storage
        self.settings = settings
        self.services = services            # False — в тестах: без таймеров, systemd и проверок Claude
        self.last_activity = time.time()
        self.claude_status: dict = {"ok": False, "installed": True, "message": "Проверяю вход в Claude…",
                                    "checked": False}
        self.bridge = Bridge()
        self.notices: dict = {}
        self.feed = None
        self._ranker: Ranker | None = None
        self._ranker_at = 0.0
        self._dirty = False
        self._feed_gen = 0
        self._feed_built_at = 0.0
        self._signals_since_build = 0
        self.collecting: str | None = None      # "collect" | "search" | None
        self._cancel = threading.Event()
        self._external_run = False
        self._last_poll = time.time()
        self._day = time.strftime("%Y-%m-%d")
        self._weekly_running = False
        self._weekly_checked = time.time()
        self._login_watch = 0
        self.window = None
        self.tray = None
        self._threads: list[threading.Thread] = []

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
            self.app.applicationStateChanged.connect(self._app_state)
        self._heal_runs()
        if self.storage.problem:
            self.set_notice("storage", "error", self.storage.problem, "Понятно", lambda: self.clear_notice("storage"))
        if self.services:
            from .ui.tray import Tray
            self.tray = Tray(self) if Tray.available() else None
        show = not (background and self.tray is not None)
        if show:
            self.window.show()
        self.refresh_feed()
        self.update_status()
        if self.services:
            self.check_claude(quiet=True)
            self.spawn(self._ensure_timer)
            if self.settings.get("ui.welcome_done") and self.settings.get("ui.autostart", True):
                self.set_autostart(True)
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self._poll)
            self._poll_timer.start(POLL_MS)
            QTimer.singleShot(90_000, self._maybe_catch_up)      # сначала даём шанс таймеру systemd
            QTimer.singleShot(120_000, self._maybe_weekly)
        if not self.settings.get("ui.welcome_done"):
            QTimer.singleShot(350, self.show_welcome)

    def shutdown(self) -> None:
        self._cancel.set()
        if self.window is not None and self.window.stack.currentWidget() is self.window.reader:
            self.window.reader.close_session()
        try:
            (STATE_DIR / "gui.pid").unlink()
        except OSError:
            pass

    def _heal_runs(self) -> None:
        """Запуски, оставшиеся «идущими» после сбоя или выключения ПК, помечаем прерванными."""
        try:
            self.storage.execute("UPDATE runs SET status='failed', error='прерван (ПК выключен или сбой)', "
                                 "finished=? WHERE status='running' AND started < ?",
                                 (time.time(), time.time() - 2 * 3600))
        except Exception:  # noqa: BLE001
            log.exception("починка журнала запусков")

    # ================================================================ потоки
    def ui(self, fn, *args) -> None:
        """Выполнить в потоке окна."""
        self.bridge.call.emit(lambda: fn(*args))

    def spawn(self, fn, done=None) -> threading.Thread:
        def work():
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                log.exception("фоновая задача")
                res = {"status": "failed", "ok": False, "message": f"Что-то пошло не так: {exc}"}
            if done is not None:
                self.ui(done, res)
        t = threading.Thread(target=work, daemon=True)
        self._threads = [x for x in self._threads if x.is_alive()] + [t]
        t.start()
        return t

    # ================================================================ лента и ранжирование
    def ranker(self) -> Ranker:
        now = time.time()
        if self._ranker is None or self._dirty or now - self._ranker_at > 300:
            self._ranker = Ranker(self.storage, self.settings, now=now)
            self._ranker_at = now
            self._dirty = False
        return self._ranker

    def _changed(self) -> None:
        """Новое действие читателя: модель пересчитается при следующем обращении."""
        self._dirty = True
        self._signals_since_build += 1

    def refresh_feed(self) -> None:
        self._feed_gen += 1
        gen = self._feed_gen

        def work():
            r = Ranker(self.storage, self.settings)
            feed = r.build()
            liked = {aid for aid, s in r.model.signals.items()
                     if s.reactions.get("like") or s.reactions.get("superlike")}
            for it in feed.all_items():
                if it.id in liked:
                    it.article["_liked"] = True
            return r, feed

        def done(res):
            if gen != self._feed_gen or self.window is None:
                return
            if isinstance(res, dict):
                self.set_notice("feed", "error", "Не удалось собрать ленту: " + res.get("message", ""),
                                "Повторить", self.refresh_feed)
                return
            self.clear_notice("feed")
            r, feed = res
            self._ranker, self._ranker_at, self._dirty = r, time.time(), False
            self.feed = feed
            self._feed_built_at = time.time()
            self._signals_since_build = 0
            self.window.feed_page.set_feed(feed)
            if self.tray is not None:
                self.tray.set_unread(any(it.article.get("status") == "new" for it in feed.main))
        self.spawn(work, done)

    def log_impression(self, aid: int, position: int, ms: int, item) -> None:
        self.storage.log_impression(aid, position, ms, "feed", randomized=getattr(item, "randomized", False),
                                    parts=getattr(item, "parts", None))

    def _lists(self) -> list:
        w = self.window
        return [w.feed_page.list, w.search_page.list, w.saved_page.list] if w else []

    def _update_rows(self, aid: int, **fields) -> None:
        for lst in self._lists():
            lst.model().refresh_article(aid, **fields)
            lst._relayout()

    def _remove_rows(self, aid: int) -> None:
        for lst in self._lists():
            lst.model().remove_article(aid)

    # ================================================================ действия читателя
    def open_article(self, aid: int) -> None:
        self.window.open_reader(aid)

    def reaction_state(self, aid: int) -> dict:
        state: dict = {}
        for r in self.storage.query("SELECT kind, value FROM events WHERE article_id=? AND kind IN (%s) "
                                    "ORDER BY ts, id" % ",".join("?" * len(TOGGLE_KINDS)), (aid, *TOGGLE_KINDS)):
            state[r["kind"]] = float(r["value"]) > 0
        return state

    def react(self, aid: int, kind: str, on: bool) -> None:
        self.storage.log_event(aid, kind, 1.0 if on else 0.0)
        if kind == "follow":
            self.storage.update_article(aid, followed=int(on))
        if kind in ("like", "superlike"):
            self._update_rows(aid, _liked=on)
        self._changed()

    def set_saved(self, aid: int, on: bool) -> None:
        self.storage.update_article(aid, saved=int(on))
        self.storage.log_event(aid, "save", 1.0 if on else 0.0)
        self._update_rows(aid, saved=int(on))
        self._changed()

    def feed_action(self, aid: int, key: str, gpos) -> None:
        a = self.storage.article(aid)
        if not a:
            return
        if key == "like":
            state = self.reaction_state(aid)
            on = not state.get("like")
            if on and state.get("dislike"):
                self.react(aid, "dislike", False)
            self.react(aid, "like", on)
            self.toast("Учту — больше такого" if on else "Отметка снята")
        elif key == "save":
            on = not bool(a.get("saved"))
            self.set_saved(aid, on)
            self.toast("Сохранено — в разделе «Сохранённое»" if on else "Убрано из сохранённого")
        elif key == "dislike":
            menu = QMenu(self.window)
            for kind, label in HIDE_REASONS:
                menu.addAction(label, lambda k=kind: self.hide_article(aid, k))
            menu.addSeparator()
            if a.get("domain"):
                menu.addAction(f"Не показывать {a['domain']}", lambda: self.add_rule("mute_source", a["domain"]))
            if a.get("topic"):
                menu.addAction(f"Не показывать тему «{a['topic']}»", lambda: self.add_rule("mute_topic", a["topic"]))
            menu.exec(gpos)
        elif key == "more":
            menu = QMenu(self.window)
            menu.addAction("Открыть на сайте", lambda: self.open_url(a["url"]))
            menu.addAction("Почему эта статья здесь", lambda: self.explain(aid, self.window))
            followed = self.reaction_state(aid).get("follow")
            menu.addAction("Не следить за сюжетом" if followed else "Следить за сюжетом",
                           lambda: (self.react(aid, "follow", not followed),
                                    self.toast("Продолжения сюжета будут выше в ленте" if not followed
                                               else "Больше не слежу за сюжетом")))
            for e in (a.get("entities") or [])[:2]:
                menu.addAction(f"Не показывать про «{e}»", lambda e=e: self.add_rule("mute_entity", e))
            menu.exec(gpos)

    def hide_article(self, aid: int, reason: str) -> None:
        """«Не моё» в ленте: статья исчезает, причина идёт в обучение, можно отменить."""
        a = self.storage.article(aid)
        if not a:
            return
        prev = a.get("status") or "new"
        self.react(aid, reason, True)
        self.storage.update_article(aid, status="hidden")
        self._remove_rows(aid)
        label = dict(HIDE_REASONS).get(reason, "")

        def undo():
            self.react(aid, reason, False)
            self.storage.update_article(aid, status=prev)
            self.refresh_feed()
        self.toast(f"Скрыто: «{label}» — лента учтёт", "Отменить", undo)

    def add_rule(self, kind: str, target: str) -> None:
        self.storage.add_rule(kind, target)
        row = self.storage.one("SELECT max(id) AS id FROM rules")
        rid = row["id"] if row else None
        self._changed()
        self.refresh_feed()
        what = {"mute_source": f"Источник {target}", "mute_topic": f"Тема «{target}»",
                "mute_entity": f"Статьи про «{target}»"}.get(kind, target)

        def undo():
            if rid is not None:
                self.storage.remove_rule(rid)
            self.refresh_feed()
        self.toast(f"{what} больше не показываются", "Отменить", undo)

    def should_survey(self, aid: int) -> bool:
        """Опрос «Стоило времени?» — изредка, без навязчивости, только где нет явной оценки."""
        if not self.settings.get("learning.personalization", True):
            return False
        every = max(1, int(self.settings.get("learning.survey_every", 4)))
        if self.storage.surveys_today() >= int(self.settings.get("learning.survey_max_per_day", 3)):
            return False
        if self.storage.one("SELECT 1 FROM surveys WHERE article_id=?", (aid,)):
            return False
        state = self.reaction_state(aid)
        if any(state.get(k) for k in EXPLICIT):
            return False
        a = self.storage.article(aid) or {}
        if int(a.get("words") or 0) < 250:
            return False
        return (aid * 2654435761) % 2**32 / 2**32 < 1.0 / every

    def survey(self, aid: int, stars: int) -> None:
        self.storage.log_survey(aid, stars)
        self._changed()

    def similar(self, article: dict) -> list:
        try:
            return self.ranker().similar(article)
        except Exception:  # noqa: BLE001
            log.exception("похожее")
            return []

    def finish_read(self, aid: int, active_ms: int, max_scroll: float, expected_ms: int, mode: str,
                    pos: float) -> None:
        a = self.storage.article(aid)
        if not a:
            return
        fields: dict = {"read_pos": round(float(pos), 3)}
        if active_ms >= 1000:
            self.storage.log_read(aid, active_ms, max_scroll, expected_ms, mode)
            self._changed()
        status = a.get("status")
        if status != "hidden":
            if max_scroll >= 0.9 or active_ms >= 0.6 * max(1, expected_ms):
                fields["status"] = "read"
            elif status == "new":
                fields["status"] = "opened"
        self.storage.update_article(aid, **fields)
        self._update_rows(aid, **fields)

    def after_reading(self) -> None:
        if time.time() - self._feed_built_at > FEED_STALE_S or self._signals_since_build >= FEED_SIGNALS_REBUILD:
            self.refresh_feed()

    def open_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def explain(self, aid: int, parent=None) -> None:
        from .ui.dialogs import explain_dialog
        a = self.storage.article(aid)
        if not a:
            return
        item = next((it for it in (self.feed.all_items() if self.feed else []) if it.id == aid), None)
        ranker = self.ranker()
        if item is None:
            item = ranker.score(a, sample=False)
        explain_dialog(parent or self.window, item, ranker.model)

    # ================================================================ Claude: перевод, вопрос
    def translate(self, aid: int, on_block, on_done, on_progress) -> None:
        from . import translate as tr

        def current() -> bool:
            r = self.window.reader if self.window else None
            return bool(r and r.article and r.article.get("id") == aid)

        def block(idx, text):
            self.ui(lambda: current() and on_block(idx, text))

        def progress(msg):
            self.ui(lambda: current() and on_progress(msg))

        def done(res):
            if res.get("error_kind") in ("auth", "limit", "not_installed"):
                self._claude_failed(res["error_kind"], res.get("message", ""))
            if current():
                on_done(res)
        self.spawn(lambda: tr.translate_article(self.settings, self.storage, aid, on_block=block,
                                                on_progress=progress, cancel=self._cancel), done)

    def ask(self, aid: int, question: str, cb) -> None:
        a = self.storage.article(aid)
        if not a:
            return
        self.storage.log_event(aid, "ask", 1.0, meta=question[:500])
        self._changed()
        blocks = self.storage.blocks(aid)
        text = "\n\n".join((b["text_ru"] or b["text_orig"]) for b in blocks if b["type"] != "img")
        words = text.split()
        if len(words) > 7000:
            text = " ".join(words[:7000]) + " …"
        payload = {"заголовок": a.get("title_ru") or a.get("title_orig"), "источник": a.get("source") or a.get("domain"),
                   "дата": time.strftime("%d.%m.%Y", time.localtime(a.get("published_at") or a["collected_at"])),
                   "текст": text or "\n".join(a.get("summary_ru") or []), "вопрос": question}

        def work():
            try:
                res = claude_cli.run(
                    "Ответь на вопрос читателя по статье из входных данных.",
                    ["--system-prompt-file", str(PROMPTS_DIR / "ask.md"), "--tools", "", "--strict-mcp-config",
                     "--mcp-config", '{"mcpServers":{}}', "--model", self.settings.get("claude.model", "sonnet"),
                     "--max-turns", "2", "--no-session-persistence"],
                    command=self.settings.get("claude.command", "claude"),
                    stdin=json.dumps(payload, ensure_ascii=False), cwd=str(RUNTIME_DIR), timeout=300)
                return (res.text or "").strip() or "Claude не ответил — попробуйте спросить иначе."
            except claude_cli.ClaudeError as exc:
                self.ui(self._claude_failed, exc.kind, exc.human())
                return exc.human()
        ensure_dirs()
        self.spawn(work, lambda text: cb(text if isinstance(text, str) else text.get("message", "")))

    # ================================================================ сбор и поиск
    def collect_now(self, auto: bool = False) -> None:
        if self.collecting:
            if not auto and self.collecting == "collect":
                self._cancel.set()
                self.toast("Останавливаю сбор… Уже найденное сохранится.")
            return
        if not auto and not self.claude_status.get("installed", True):
            self.show_install_help()
            return
        if self._external_run:
            if not auto:
                self.toast("Сбор по расписанию уже идёт — лента обновится сама.")
            return
        self.collecting = "collect"
        self._cancel = threading.Event()
        fp = self.window.feed_page
        fp.progress.setText("Claude начинает сбор…")
        fp.progress.show()
        fp.refresh_btn.setText("Остановить")
        self.update_status("Идёт сбор…")
        cancel = self._cancel

        def work():
            return collect.run(self.settings, self.storage, force=not auto, db_path=self.storage.path,
                               on_progress=lambda m: self.ui(self._progress, m), cancel=cancel)

        def done(res):
            self.collecting = None
            fp.progress.hide()
            fp.refresh_btn.setText("Собрать сейчас")
            self._handle_run(res, manual=not auto)
        self.spawn(work, done)

    def _progress(self, msg: str) -> None:
        if self.window is not None:
            self.window.feed_page.progress.setText(msg)

    def web_search(self, query: str, on_progress, on_done) -> None:
        if self.collecting or self._external_run:
            on_done({"status": "busy", "message": "Сейчас идёт сбор — поиск можно запустить через несколько минут."})
            return
        self.collecting = "search"
        self._cancel = threading.Event()
        cancel = self._cancel

        def work():
            return collect.run(self.settings, self.storage, kind="search", query=query, force=True,
                               db_path=self.storage.path, on_progress=lambda m: self.ui(on_progress, m),
                               cancel=cancel)

        def done(res):
            self.collecting = None
            if res.get("status") == "ok":
                res = dict(res, message=f"Нашлось новых материалов: {res['saved']}. Они ниже и в Ленте.")
            elif res.get("status") == "empty":
                res = dict(res, message="Нового по запросу не нашлось — всё найденное уже есть в библиотеке.")
            self._handle_run(res, manual=False)
            on_done(res)
        self.spawn(work, done)

    def _handle_run(self, res: dict, manual: bool) -> None:
        status, kind = res.get("status"), res.get("error_kind", "")
        if status in ("ok", "empty"):
            for k in ("run", "claude"):
                self.clear_notice(k)
            if not self.claude_status.get("ok"):
                self.claude_status = {"ok": True, "installed": True, "message": "", "checked": True}
                self.claude_changed.emit()
        if kind:
            self._claude_failed(kind, res.get("message", ""))
        elif status == "failed":
            self.set_notice("run", "error", res.get("message", "Сбор не удался."), "Повторить",
                            lambda: (self.clear_notice("run"), self.collect_now()))
        if manual:
            if status == "skipped":
                self.toast("Недавно уже собирали — новое появится к следующему сбору.")
            elif status == "busy":
                self.toast("Сбор уже идёт — лента обновится сама.")
            elif status in ("ok", "empty", "partial"):
                self.toast(res.get("message", ""))
        if res.get("saved"):
            self.refresh_feed()
            self._notify_new(int(res["saved"]))
        self.update_status()

    def _claude_failed(self, kind: str, message: str) -> None:
        """Понятная причина и кнопка, которая её исправляет."""
        if kind == "not_installed":
            self.claude_status = {"ok": False, "installed": False, "message": message, "checked": True}
            self.set_notice("claude", "error", "Claude Code не установлен — без него лента не соберётся.",
                            "Как установить", self.show_install_help)
        elif kind == "auth":
            self.claude_status = {"ok": False, "installed": True, "message": message, "checked": True}
            self.set_notice("claude", "error", "Вход в Claude истёк или не выполнен.", "Войти в Claude",
                            self.login_claude)
        elif kind == "limit":
            self.set_notice("run", "info", "Лимит подписки Claude на время исчерпан. Сбор повторится сам, "
                            "когда лимит обновится.", None, None)
        elif kind == "network":
            self.set_notice("run", "info", "Нет связи с Claude — повторю сам, когда появится сеть.", "Повторить",
                            lambda: (self.clear_notice("run"), self.collect_now()))
        elif kind == "timeout":
            self.set_notice("run", "info", message or "Claude слишком долго не отвечал.", "Повторить",
                            lambda: (self.clear_notice("run"), self.collect_now()))
        self.claude_changed.emit()

    def _notify_new(self, n: int) -> None:
        if not self.settings.get("ui.notifications", True) or self.window is None:
            return
        if self.window.isVisible() and self.window.isActiveWindow():
            return
        text = f"Свежих статей: {n}"
        if self.tray is not None:
            self.tray.message(APP_NAME, text)
        elif self.services:
            threading.Thread(target=collect.notify, args=(APP_NAME, text), daemon=True).start()

    # ================================================================ фоновые проверки
    def _poll(self) -> None:
        now = time.time()
        resumed = now - self._last_poll > 5 * 60          # ПК просыпался
        self._last_poll = now
        running = self.storage.one("SELECT id FROM runs WHERE status='running' AND started > ? ORDER BY id DESC "
                                   "LIMIT 1", (now - RUNNING_STALE_S,))
        if running and not self.collecting and not self._external_run:
            self._external_run = True
            fp = self.window.feed_page
            fp.progress.setText("Идёт сбор по расписанию…")
            fp.progress.show()
            self.update_status("Идёт сбор…")
        elif self._external_run and not running:
            self._external_run = False
            self.window.feed_page.progress.hide()
            last = self.storage.last_run("collect", ok_only=False) or {}
            err = (last.get("error") or "")
            kind = err.split(":", 1)[0] if ":" in err else ""
            self._handle_run({"status": last.get("status"), "saved": last.get("saved", 0),
                              "message": err, "error_kind": kind if kind in (
                                  "auth", "limit", "network", "not_installed", "timeout") else ""}, manual=False)
        day = time.strftime("%Y-%m-%d")
        if day != self._day:                    # новый день — новая выборка Томпсона
            self._day = day
            self.refresh_feed()
        if resumed:
            self.check_claude(quiet=True)
        self._maybe_catch_up()
        if now - self._weekly_checked > 3600:
            self._weekly_checked = now
            self._maybe_weekly()

    def _maybe_catch_up(self) -> None:
        """ПК был выключен в плановое время — догоняем сбор сами."""
        if self.collecting or self._external_run or not self.settings.get("ui.welcome_done"):
            return
        if not self.settings.get("schedule.enabled", True) or not self.claude_status.get("ok"):
            return
        last_any = self.storage.last_run("collect", ok_only=False)
        if last_any and last_any["status"] == "failed":
            kind = (last_any.get("error") or "").split(":", 1)[0]
            wait = {"limit": 3600, "network": 900}.get(kind, 3 * 3600)
            if time.time() - float(last_any.get("finished") or last_any["started"]) < wait:
                return
        if collect.due(self.settings, self.storage):
            self.collect_now(auto=True)

    def _maybe_weekly(self) -> None:
        if self._weekly_running or self.collecting or not self.settings.get("learning.weekly_review", True):
            return
        if not self.claude_status.get("ok") or not weekly.due(self.storage):
            return
        self.weekly_now(None, quiet=True)

    def weekly_now(self, cb, quiet: bool = False) -> None:
        if self._weekly_running:
            return
        self._weekly_running = True
        if not quiet:
            self.toast("Claude разбирает вашу неделю — около минуты…")

        def done(res):
            self._weekly_running = False
            if res.get("ok"):
                self.set_notice("weekly", "info", "Claude разобрал вашу неделю и предлагает правки профиля и тем.",
                                "Посмотреть", lambda: (self.clear_notice("weekly"), self.window.open_page("settings")))
            elif not quiet:
                self.toast(res.get("message", "Разбор не удался."))
            self._changed()
            if cb:
                cb(res)
        self.spawn(lambda: weekly.run(self.settings, self.storage, force=not quiet), done)

    def import_takeout(self, path: str, cb) -> None:
        self.toast("Читаю выгрузку Google и отправляю Claude названия… Это 1–2 минуты.")

        def done(res):
            self.toast(res.get("message", ""), ms=9000)
            self._changed()
            self.refresh_feed()
            if cb:
                cb(res)
        from . import takeout
        self.spawn(lambda: takeout.import_takeout(self.settings, self.storage, path), done)

    def _app_state(self, state) -> None:
        """Вернулись к окну после перерыва — свежая лента (если вы не посреди статьи)."""
        if state != Qt.ApplicationActive or self.window is None:
            return
        if time.time() - self._feed_built_at > 30 * 60 and self.window.stack.currentWidget() is not self.window.reader:
            self.refresh_feed()

    # ================================================================ Claude: вход и установка
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
            if self.window is not None and self.window.current == "settings" and \
                    self.window.stack.currentWidget() is self.window.settings_page.area:
                self.window.settings_page.rebuild()
        self.spawn(lambda: claude_cli.auth_status(cmd), done)

    def login_claude(self) -> None:
        exe = claude_cli.find_claude(self.settings.get("claude.command", "claude"))
        if not exe:
            self.show_install_help()
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

    def show_install_help(self) -> None:
        from .ui.dialogs import message_dialog

        def install():
            cmd = INSTALL_CMD + " && ~/.local/bin/claude auth login"
            if open_terminal(cmd):
                self.toast("Установка идёт в терминале. После входа «Сводка» заметит это сама.", ms=8000)
                self._watch_login()
        message_dialog(
            self.window, "Нужен Claude Code",
            "«Сводка» ищет, читает и переводит статьи с помощью Claude Code по вашей подписке Pro — "
            "ключи API и отдельная модель не нужны. Установка занимает минуту:",
            code=INSTALL_CMD + "\nclaude auth login",
            buttons=[("Установить в терминале", install, True), ("Проверить снова", lambda: self.check_claude(), False)])

    # ================================================================ расписание, автозапуск, вид
    def _ensure_timer(self) -> dict:
        """Самопочинка расписания: таймер systemd на месте и совпадает с настройками."""
        ok, msg = systemd.ensure(self.settings.get("schedule.times") or ["07:37", "18:37"],
                                 bool(self.settings.get("schedule.enabled", True)))
        if not ok and msg:
            self.ui(self.set_notice, "timer", "info", f"Расписание сбора не работает ({msg[:120]}). Пока окно открыто, "
                    "«Сводка» соберёт ленту сама.", "Повторить", self.reinstall_timer)
        else:
            self.ui(self.clear_notice, "timer")
        return {"ok": ok}

    def reinstall_timer(self) -> None:
        def work():
            times = self.settings.get("schedule.times") or ["07:37", "18:37"]
            return systemd.install(times, enable=bool(self.settings.get("schedule.enabled", True)))

        def done(res):
            if isinstance(res, tuple) and res[0]:
                self.clear_notice("timer")
                self.update_status()
            else:
                msg = res[1] if isinstance(res, tuple) else res.get("message", "")
                self.set_notice("timer", "info", f"Не удалось обновить расписание: {msg[:160]}", "Повторить",
                                self.reinstall_timer)
        if self.services:
            self.spawn(work, done)

    def set_autostart(self, on: bool) -> None:
        try:
            if on:
                AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
                text = desktop_entry(["gui", "--background"], background=True)
                if not AUTOSTART_FILE.exists() or AUTOSTART_FILE.read_text(encoding="utf-8") != text:
                    AUTOSTART_FILE.write_text(text, encoding="utf-8")
            elif AUTOSTART_FILE.exists():
                AUTOSTART_FILE.unlink()
        except OSError as exc:
            log.warning("автозапуск: %s", exc)

    def apply_theme(self) -> None:
        if self.window is not None:
            self.window.apply_theme()

    def next_collect_text(self) -> str:
        if not self.settings.get("schedule.enabled", True):
            return ""
        now = time.localtime()
        cur = now.tm_hour * 60 + now.tm_min
        times = sorted(self.settings.get("schedule.times") or [])
        for t in times:
            h, m = (int(x) for x in t.split(":"))
            if h * 60 + m > cur:
                return f"сегодня в {t}"
        return f"завтра в {times[0]}" if times else ""

    def collect_status_text(self) -> str:
        parts = []
        last = self.storage.last_run("collect", ok_only=False)
        if last:
            st = {"ok": f"статей {last['saved']}", "empty": "нового не было", "partial": f"частично, {last['saved']}",
                  "failed": "не удался", "running": "идёт"}.get(last["status"], last["status"])
            parts.append(f"Последний сбор {when_text(last['started'])} — {st}")
        else:
            parts.append("Сборов ещё не было")
        nxt = self.next_collect_text()
        parts.append(f"следующий {nxt}" if nxt else "расписание выключено")
        return ", ".join(parts) + "."

    def update_status(self, text: str | None = None) -> None:
        if self.window is None:
            return
        if text is None:
            last = self.storage.last_run("collect")
            nxt = self.next_collect_text()
            text = (f"Обновлено {when_text(last['started'])}" if last else "Лента ещё не собиралась") + \
                   (f"\nСледующий сбор {nxt}" if nxt else "")
        self.window.set_status(text)

    # ================================================================ уведомления в окне
    def set_notice(self, key: str, kind: str, text: str, label: str | None, fn) -> None:
        self.notices[key] = (kind, text, label, fn)
        if self.window is not None:
            self.window.feed_page.render_notices(self.notices)

    def clear_notice(self, key: str) -> None:
        if self.notices.pop(key, None) is not None and self.window is not None:
            self.window.feed_page.render_notices(self.notices)

    def toast(self, text: str, action: str | None = None, fn=None, ms: int = 5000) -> None:
        if self.window is not None and text:
            self.window.toast(text, action, fn, ms)

    def show_welcome(self) -> None:
        from .ui.welcome import Welcome
        Welcome(self, self.window).exec()


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
