"""Контроллер приложения: клавиши → запись → распознавание → обработка → вставка.

Каждая диктовка — отдельная сессия со своим номером (sid). Пока предыдущая фраза
распознаётся и исправляется, уже можно говорить следующую: результаты складываются
в очередь и вставляются строго по порядку.

Клавиша (по умолчанию Правый Alt):
  * удерживать и говорить → отпустить = вставить (push-to-talk);
  * коротко нажать (или дважды) = длинная запись «без рук»; следующее нажатие — закончить;
  * щелчок по облачку = длинная запись, красная кнопка / ещё щелчок = закончить;
  * Esc — отменить текущую запись (ещё раз — и обработку предыдущих).
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from . import llm, perf, pwaudio
from .asr.engine import ASREngine
from .audio import Recorder, SoundPlayer, audio_backend_error, clipped_count, friendly_mic_error
from .config import APP_ID, Settings
from .corrector import Corrector
from .hotkeys import (MODIFIER_GENERIC, MODIFIER_TOKENS, HotkeyRecorder, KeyState, SelectionTracker,
                      X11Grabber, X11KeyListener, pretty_combo, read_x_selection)
from .inserter import X11Desktop, WindowInfo, xdotool_type
from .localllm import LocalLLM
from .postprocess import TextProcessor, casual, count_words, is_messenger, split_send_it
from .storage import (DEFAULT_DICTIONARY, DEFAULT_REPLACEMENTS, History, JsonStore, migrate_dictionary,
                      save_wav)
from .sysaudio import SystemAudio

log = logging.getLogger(__name__)

IPC_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"{APP_ID}-{os.getuid()}.sock")
SAFE_MIME = ("text/", "image/png", "x-special/", "application/x-kde", "chromium/")
CLIP_WARN_RATIO = 0.01     # больше 1 % сэмплов в потолок — микрофон перегружен
CLIP_AUTOFIX_RATIO = 0.05  # больше 5 % — снижаем усиление сами (один раз, с кнопкой «Вернуть»)
CLIP_SKIP_SAMPLES = 5600   # первые 0,35 с записи — щелчок при включении микрофона не считаем
MIN_SPEECH_REPORT_S = 0.8  # «Речь не распознана» показываем только для записей длиннее
WATCHDOG_MS = 2000


def guarded(fn):
    """Сбой в обработчике не должен ломать приложение: пишем в журнал и восстанавливаемся."""
    @functools.wraps(fn)
    def wrapper(self, *args):
        try:
            return fn(self, *args)
        except Exception:  # noqa: BLE001
            log.exception("Сбой в %s — восстанавливаюсь", fn.__name__)
            try:
                self._heal(fn.__name__, args)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось восстановиться после сбоя")
    return wrapper


class Session:
    def __init__(self, sid: int, mode: str, kind: str, window: WindowInfo, selection: str = ""):
        self.sid = sid
        self.mode = mode            # hold | hands_free
        self.kind = kind            # dictation | edit
        self.window = window
        self.selection = selection
        self.started = time.monotonic()
        self.t_down = self.started  # когда нажали клавишу (для «короткое нажатие или удержание»)
        self.tap_at: Optional[float] = None
        self.sink = None            # приёмник аудио этой сессии (привязан к sid)
        self.samples = 0
        self.clipped = 0
        self.clip_blocks = 0
        self.info: dict = {}
        self.raw = ""
        self.ai = False
        self.send = False
        self.text: Optional[str] = None   # готовый к вставке текст; None — ещё обрабатывается
        self.skip = False                 # вставлять нечего (пусто/ошибка)
        self.audio: list = []             # копия звука: при сбое распознавания — распознать заново
        self.last_block = self.started    # когда пришёл последний блок (микрофон «замолчал»?)
        self.asr_since: Optional[float] = None   # когда отдали распознавателю
        self.ai_since: Optional[float] = None    # когда отдали ИИ
        self.asr_retries = 0
        self.reasr = False                # распознать целиком из self.audio (распознаватель перезапущен)


class App(QObject):
    # Мосты из фоновых потоков в главный поток Qt
    sig_key = Signal(str, str, float)
    sig_level = Signal(float)
    sig_status = Signal(str, str)
    sig_partial = Signal(int, str)
    sig_final = Signal(int, str, object)
    sig_llm = Signal(int, str, str)
    sig_retranscribed = Signal(int, str)
    sig_chunk = Signal(int, int, str)
    sig_corrected = Signal(int, str)
    sig_llm_state = Signal(str, str, float)
    sig_cloud_failed = Signal(str, bool)
    sig_cloud_ok = Signal()
    sig_reasr = Signal(int, str)
    sig_clip = Signal(float, float)
    sig_power = Signal()
    sig_heal_engine = Signal(str)
    sig_need_local = Signal()
    sig_notice_done = Signal(str, bool, str)
    # Для окна настроек
    status_changed = Signal(str, str)
    history_changed = Signal()
    hotkey_captured = Signal(list, bool)    # (сочетание, окончательно)
    mic_level = Signal(float)
    activate_seen = Signal()
    llm_state_changed = Signal(str, str, float)
    notices_changed = Signal()

    def __init__(self, qapp: QApplication, settings: Settings):
        super().__init__()
        self.qapp = qapp
        self.settings = settings
        self.history = History()
        self.dictionary = JsonStore("dictionary.json", DEFAULT_DICTIONARY)
        migrate_dictionary(self.dictionary)
        self.replacements = JsonStore("replacements.json", DEFAULT_REPLACEMENTS)
        self.processor = TextProcessor()

        self.sounds = SoundPlayer(settings.get("audio.sound_volume", 0.35))
        self.sysaudio = SystemAudio()
        self.desktop = X11Desktop()
        self.keys = KeyState()
        self.hotkey_recorder = HotkeyRecorder()
        self.grabber: Optional[X11Grabber] = None
        self.listener: Optional[X11KeyListener] = None
        self.selection = SelectionTracker()

        self.engine = self._new_engine()
        self.local_llm = LocalLLM(settings, self.sig_llm_state.emit)
        self.router = llm.Router(settings, self.local_llm)
        self.router.on_cloud_failed = self.sig_cloud_failed.emit
        self.router.on_cloud_ok = self.sig_cloud_ok.emit
        self.router.on_need_local = self.sig_need_local.emit
        self.corrector = Corrector(settings, self.local_llm, self.router)
        self.reload_dictionary()
        self.recorder = Recorder(self.sig_level.emit)
        self.engine_state = ("loading", "Загрузка…")

        self.rec: Optional[Session] = None          # идёт запись
        self.jobs: dict[int, Session] = {}          # записаны, ещё обрабатываются (по порядку sid)
        self._tail: Optional[Session] = None        # дописываем «хвост» после отпускания клавиши
        self._sid = 0
        self._act_down = False
        self._press_sid: Optional[int] = None       # какую запись начало текущее нажатие клавиши
        self._live_sid: Optional[int] = None
        self._ignore_names: dict[str, int] = {}
        self._last_text = ""
        self._last_insert = None
        self._mic_test = False
        self._mic_test_since = 0.0
        self._mic_sink = lambda block: None
        self._clip_history: deque = deque(maxlen=3)
        self._cancel_grabbed = False
        self._outbox: deque = deque()               # очередь вставки (text, send)
        self._insert_busy = False
        self._insert_wait_since: Optional[float] = None
        self._clip_restore: Optional[QMimeData] = None
        self._clip_text = ""
        self._clip_warned_at = 0.0
        self._llm_started_once = False
        self._insert_started = 0.0
        self._load_retries = 0
        self._clip_autofixed_at = 0.0
        self.notices: dict[str, tuple[str, str, str]] = {}   # key → (kind, текст, кнопка)

        from .ui.bubble import Bubble
        self.bubble = Bubble(settings)
        self.bubble.clicked.connect(self._bubble_clicked)
        self.bubble.stop_clicked.connect(self.finish)
        self.bubble.menu_requested.connect(self._bubble_menu)

        self.sig_key.connect(self._on_key)
        self.sig_level.connect(self._on_level)
        self.sig_status.connect(self._on_status)
        self.sig_partial.connect(self._on_partial)
        self.sig_final.connect(self._on_final)
        self.sig_llm.connect(self._on_llm)
        self.sig_retranscribed.connect(self._on_retranscribed)
        self.sig_cloud_ok.connect(lambda: self.clear_notice("cloud"))
        self.sig_reasr.connect(self._on_reasr)
        self.sig_clip.connect(self._on_clip)
        self.sig_power.connect(self._on_power)
        self.sig_heal_engine.connect(self._restart_engine)
        self.sig_corrected.connect(self._on_corrected)
        self.sig_llm_state.connect(self._on_llm_state)
        self.sig_cloud_failed.connect(self._on_cloud_failed)
        self.sig_need_local.connect(self._start_local_fallback)
        self.sig_notice_done.connect(self._on_notice_done)
        self._retranscribe_callbacks: dict[int, object] = {}
        self.settings.on_change(self._on_setting)

        self.window = None
        self.tray = None
        self.quitting = False
        self.server: Optional[QLocalServer] = None

    # ================================================================ состояние
    @property
    def phase(self) -> str:
        """idle | recording | processing — для меню и IPC."""
        if self.rec is not None:
            return "recording"
        return "processing" if self.jobs else "idle"

    @property
    def session(self) -> Optional[Session]:
        return self.rec

    # ================================================================ запуск
    def start_services(self) -> None:
        err = audio_backend_error()
        if err:
            log.error(err)
        try:
            self.listener = X11KeyListener(self.sig_key.emit)
            self.listener.start()
            self.grabber = X11Grabber()
        except Exception as exc:  # noqa: BLE001
            log.exception("Глобальные клавиши недоступны")
            self.listener = None
            QTimer.singleShot(1500, lambda: self.bubble.show_message(
                "Нет доступа к клавиатуре X11 — используйте «aqua-linux toggle»", "warn", 6000))
            self.engine_state = ("error", str(exc))
        self._grab_static()
        self.bubble.set_hint(self.hint_text())
        self.bubble.update_frame_rate()
        self.bubble.set_model_loading(True)
        self.bubble.apply_targets()
        if self.settings.get("bubble.show", True):
            self.bubble.reposition()
            self.bubble.show()
            self.bubble._kick()
        self.engine.start()
        # Локальную модель запускаем после распознавателя (он важнее для видеопамяти);
        # запасной таймер — если распознаватель грузится очень долго.
        QTimer.singleShot(20000, self._maybe_start_llm_once)
        if self.settings.get("audio.keep_mic_warm") and not perf.economy(self.settings):
            self.recorder.set_warm(True, self.settings.get("audio.input_device"))
        self._start_ipc()
        # Базу терминов грузим заранее, чтобы первая диктовка не ждала.
        if self.settings.get("text.builtin_terms", False):
            from . import terms as builtin_terms
            threading.Thread(target=builtin_terms.index, name="terms", daemon=True).start()
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.timeout.connect(self._watchdog)
        self._watchdog_timer.start(WATCHDOG_MS)
        purge = QTimer(self)
        purge.timeout.connect(lambda: self.history.purge_audio(int(self.settings.get("audio.keep_audio_days", 3))))
        purge.start(3600 * 1000)
        QTimer.singleShot(5000, lambda: self.history.purge_audio(int(self.settings.get("audio.keep_audio_days", 3))))

    def quit_app(self) -> None:
        """Полный выход. QApplication.quit() в Qt 6 сначала закрывает окна, а главное окно
        при закрытии лишь прячется в трей — поэтому выходим из цикла событий напрямую."""
        self.quitting = True
        self.qapp.exit(0)

    def shutdown(self) -> None:
        # При выходе из сеанса X-сервер может закрыться раньше нас — ошибки здесь не важны.
        for step in (self._save_pending,
                     lambda: self.cancel_all(),
                     lambda: self.listener and self.listener.stop(),
                     lambda: self.grabber and self.grabber.ungrab_all(),
                     self.engine.shutdown,
                     self.local_llm.stop,
                     lambda: self.recorder.set_warm(False),
                     lambda: self.recorder.stop(),
                     self.sysaudio.end):
            try:
                step()
            except Exception:  # noqa: BLE001
                log.debug("Ошибка при завершении", exc_info=True)

    def _save_pending(self) -> None:
        """Выход во время обработки: фраза не теряется — сохраняем её в историю."""
        for sess in list(self.jobs.values()):
            text = sess.text if sess.text else (self.processor.process(sess.raw, self.settings) if sess.raw else "")
            if not text.strip() or self.settings.get("general.privacy_mode", False):
                continue
            audio_path = None
            if sess.audio and self.settings.get("audio.save_audio", True):
                try:
                    audio_path = save_wav(np.concatenate(sess.audio))
                except Exception:  # noqa: BLE001
                    pass
            self.history.add(text.strip(), sess.raw or text, sess.window.app, sess.window.title, "dictation",
                             sess.samples / 16000, count_words(text), 0.0, audio_path)

    def hint_text(self) -> str:
        combos = self.settings.get("hotkeys.activate") or []
        if not combos:
            return "Щёлкните, чтобы диктовать"
        return f"Удерживайте {pretty_combo(combos[0])}"

    def reload_dictionary(self) -> None:
        self.processor.load(self.dictionary.data.get("terms", []),
                            self.replacements.data.get("replacements", []))
        if hasattr(self, "corrector"):
            self.corrector.vocabulary = self.processor.vocabulary()

    # ================================================================ IPC
    def _start_ipc(self) -> None:
        QLocalServer.removeServer(IPC_PATH)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(IPC_PATH):
            log.warning("IPC недоступен: %s", self.server.errorString())
            return
        self.server.newConnection.connect(self._ipc_connection)

    def _ipc_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return

        def read():
            data = bytes(sock.readAll()).decode("utf-8", "replace").strip()
            for line in data.splitlines():
                self.command(line.strip())
            sock.write(b"ok\n")
            sock.flush()
            sock.disconnectFromServer()

        sock.readyRead.connect(read)

    def command(self, cmd: str) -> None:
        log.info("Команда: %s", cmd)
        if cmd == "toggle":
            if self.rec is not None:
                self.finish()
            else:
                self.start("hands_free")
        elif cmd == "start":
            if self.rec is None:
                self.start("hands_free")
        elif cmd == "stop":
            self.finish()
        elif cmd == "cancel":
            self.cancel()
        elif cmd == "paste-last":
            self.paste_last()
        elif cmd in ("show", "settings", "history"):
            self.show_window({"show": "home", "settings": "settings", "history": "history"}[cmd])
        elif cmd == "quit":
            self.quit_app()

    # ================================================================ клавиши
    def _combos(self, key: str) -> list[list[str]]:
        combos = self.settings.get(f"hotkeys.{key}") or []
        return [c for c in combos if c]

    def _grab_static(self) -> None:
        if not self.grabber:
            return
        self.grabber.ungrab_all()
        self._cancel_grabbed = False
        if not self.settings.get("hotkeys.enabled", True):
            return
        for key in ("paste_last", "hands_free"):
            for combo in self._combos(key):
                if any(t not in MODIFIER_GENERIC and t.lstrip("lr") not in MODIFIER_GENERIC for t in combo):
                    self.grabber.grab(combo)

    def _grab_cancel(self, on: bool) -> None:
        if not self.grabber or on == self._cancel_grabbed:
            return
        self._cancel_grabbed = on
        for combo in self._combos("cancel"):
            if on:
                self.grabber.grab(combo, any_modifier=len(combo) == 1)
            else:
                self.grabber.ungrab(combo, any_modifier=len(combo) == 1)

    @Slot(str, str, float)
    @guarded
    def _on_key(self, kind: str, name: str, t: float) -> None:
        if name in self._ignore_names:
            self._ignore_names[name] -= 1
            if self._ignore_names[name] <= 0:
                del self._ignore_names[name]
            return
        changed = self.keys.press(name) if kind == "press" else self.keys.release(name)
        if not changed:
            return
        self.selection.note_input(kind, name, self.keys)
        if self.hotkey_recorder.active:
            self.hotkey_recorder.feed(kind, name)
            return
        if not self.settings.get("hotkeys.enabled", True):
            return

        activate = self._combos("activate")
        if kind == "press":
            if (self.rec is not None or self.jobs) and any(self.keys.matches(c) for c in self._combos("cancel")):
                self.cancel()
                return
            if any(self.keys.matches(c) for c in self._combos("paste_last")):
                if self.rec is not None and self.rec.mode == "hold":
                    self._drop_recording(silent=True)
                self.paste_last()
                return
            if any(self.keys.matches(c) for c in self._combos("hands_free")):
                if self.rec is not None:
                    self.finish()
                else:
                    self.start("hands_free", t)
                return
            if any(self.keys.matches(c) for c in activate):
                if not self._act_down:
                    self._act_down = True
                    self.activate_seen.emit()
                    self._activate_press(t)
                    self._neutralize_modifier()
                return
            # Посторонняя клавиша во время удержания — это сочетание (Alt+Tab и т.п.), не диктовка.
            if self.rec is not None and self.rec.mode == "hold" and self._act_down \
                    and name not in MODIFIER_TOKENS:
                self._drop_recording(silent=True)
        else:
            if self._act_down and not any(self.keys.contains(c) for c in activate):
                self._act_down = False
                self._activate_release(t)
            if not self.keys.any_modifier() and self._outbox:
                QTimer.singleShot(0, self._pump_insert)

    def _activate_press(self, t: float) -> None:
        rec = self.rec
        self._press_sid = None
        if rec is None:
            # Новая запись — даже если предыдущая фраза ещё обрабатывается.
            if self.start("hold", t):
                self._press_sid = self.rec.sid
        elif rec.mode == "hands_free":
            window = float(self.settings.get("hotkeys.double_tap_window_ms", 380)) / 1000
            if rec.tap_at is not None and t - rec.tap_at <= window:
                return   # второе нажатие «двойного» — запись «без рук» уже идёт
            self.finish()

    def _activate_release(self, t: float) -> None:
        rec = self.rec
        if rec is None or rec.sid != self._press_sid or rec.mode != "hold":
            return
        self._press_sid = None
        held_ms = (t - rec.t_down) * 1000
        if held_ms < float(self.settings.get("hotkeys.tap_threshold_ms", 280)) \
                and self.settings.get("hotkeys.double_tap_hands_free", True):
            # Короткое нажатие — длинная запись «без рук» (не отмена).
            rec.tap_at = t
            self._set_mode("hands_free")
        else:
            self.finish()

    def _neutralize_modifier(self) -> None:
        """Одиночный Alt/Super после отпускания открывает меню (Firefox) или обзор (GNOME).
        Пока клавиша зажата, подмешиваем нажатие свободного keycode без символа —
        для приложений это уже не «одиночный Alt», а сочетание с ничего не делающей клавишей."""
        if not self.settings.get("hotkeys.neutralize_modifier", True):
            return
        combos = self._combos("activate")
        if not any(all((t.lstrip("lr") if t[:1] in "lr" else t) in MODIFIER_GENERIC for t in c) for c in combos):
            return
        if not any(self.keys.contains(c) for c in combos):
            return
        code = self.desktop.dummy_keycode()
        if code:
            name = f"keycode{code}"
            self._ignore_names[name] = self._ignore_names.get(name, 0) + 2
            self.desktop.tap_keycode(code)

    def _bubble_clicked(self) -> None:
        if self.rec is not None:
            self.finish()
        else:
            self.start("hands_free")

    # ================================================================ запись
    def start(self, mode: str, t: Optional[float] = None) -> bool:
        if self.rec is not None:
            return False
        if self._tail is not None:
            # Предыдущая запись: отдать распознавателю; микрофон не закрываем — он сразу нужен.
            self._end_tail(self._tail, keep_open=True)
        if self._mic_test:
            self._mic_test = False
        window = self.desktop.active_window()
        kind, selection = "dictation", ""
        if self.settings.get("edit_mode.enabled", True) and self.selection.is_live():
            text = read_x_selection("PRIMARY")
            if text.strip() and len(text) <= int(self.settings.get("edit_mode.max_chars", 6000)):
                kind, selection = "edit", text
        self._sid += 1
        sess = Session(self._sid, mode, kind, window, selection)
        if t is not None:
            sess.t_down = t
        streaming = self.settings.get("asr.streaming", "hands_free")
        preview = streaming == "always" or (streaming == "hands_free" and mode == "hands_free")
        # Порядок важен: сессия в распознавателе → сессия в приложении → микрофон.
        # Тогда даже первые блоки и предзапись попадают в эту запись.
        self.engine.begin(sess.sid, preview)
        sess.sink = self._make_sink(sess)
        self.rec = sess
        if not self._open_mic(sess):
            self.rec = None
            self.engine.cancel(sess.sid)
            self._refresh()
            if self.settings.get("audio.sounds", True):
                self.sounds.play("error")
            return False
        if self.corrector.active() and self.router.cloud() is not None:
            # Пока человек говорит, заранее открываем соединение с DeepSeek.
            threading.Thread(target=llm.preconnect, args=(self.router.cloud().base,), daemon=True).start()
        self._live_sid = sess.sid
        self.bubble.set_live_text("")
        self.bubble.set_chip(f"{count_words(selection)} {plural_words(count_words(selection))} выделено"
                             if kind == "edit" else "")
        self._refresh()
        if self.settings.get("audio.sounds", True):
            self.sounds.volume = float(self.settings.get("audio.sound_volume", 0.35))
            self.sounds.play("start")
        sid = sess.sid
        # Приглушаем фон с задержкой: случайное короткое нажатие не дёргает громкость.
        QTimer.singleShot(300, lambda: self._begin_background(sid))
        limit_ms = int(float(self.settings.get("audio.max_minutes", 20)) * 60_000)
        QTimer.singleShot(limit_ms, lambda: self._limit_reached(sid))
        return True

    def _open_mic(self, sess: Session) -> bool:
        """Включить микрофон. Выбранный не открылся — сами берём системный, без вопросов."""
        device = self.settings.get("audio.input_device")
        try:
            self.recorder.start(device, sess.sink)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Микрофон %r не открылся: %s", device, exc)
            first = exc
        if device is not None:
            try:
                self.recorder.start(None, sess.sink)
                self.set_notice("mic", "info", "Выбранный микрофон недоступен — пишу с системного. "
                                "Когда он снова подключится, Aqua вернётся к нему сама.", "")
                return True
            except Exception as exc:  # noqa: BLE001
                first = exc
        reason = friendly_mic_error(first)
        self.bubble.show_message(f"Микрофон: {reason}"[:80], "error", 4000)
        self.set_notice("mic", "error", f"Не получилось включить микрофон: {reason}. Проверьте, что он "
                        "подключён и не занят другой программой — Aqua попробует снова при следующем нажатии.",
                        "Выбрать микрофон")
        return False

    def _make_sink(self, sess: Session):
        """Приёмник аудио, навсегда привязанный к своей сессии (вызывается из потока PortAudio)."""
        sid = sess.sid

        def sink(block):
            self.engine.feed(sid, block)      # движок мог быть перезапущен — берём текущий
            sess.audio.append(block)
            sess.samples += block.size
            sess.last_block = time.monotonic()
            # Первые 0,35 с не считаем: при включении микрофона (особенно после сна или в режиме
            # энергосбережения) звуковая карта даёт щелчок — это не перегруз.
            if sess.samples > CLIP_SKIP_SAMPLES:
                clipped = clipped_count(block)
                if clipped:
                    sess.clipped += clipped
                    if clipped >= 3:
                        sess.clip_blocks += 1
        return sink

    def _begin_background(self, sid: int) -> None:
        if self.rec is not None and self.rec.sid == sid:
            self.sysaudio.begin(self.settings.get("audio.while_dictating", "mute"))

    def _limit_reached(self, sid: int) -> None:
        if self.rec is not None and self.rec.sid == sid:
            self.finish()

    def _set_mode(self, mode: str) -> None:
        rec = self.rec
        if rec is None:
            return
        rec.mode = mode
        if mode == "hands_free":
            self._refresh()
            if self.settings.get("asr.streaming", "hands_free") in ("hands_free", "always"):
                self.engine.set_preview(rec.sid, True)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("lock")
            self.sysaudio.begin(self.settings.get("audio.while_dictating", "mute"))

    @Slot(float)
    def _on_level(self, level: float) -> None:
        self.bubble.set_level(level)
        self.mic_level.emit(level)

    def finish(self) -> None:
        """Закончить текущую запись: ещё tail_ms пишем «хвост», потом — распознаватель."""
        sess = self.rec
        if sess is None:
            return
        self.rec = None
        self._press_sid = None
        self.jobs[sess.sid] = sess
        if self._tail is not None:
            self._end_tail(self._tail)
        self._tail = sess
        QTimer.singleShot(int(self.settings.get("audio.tail_ms", 140)), lambda: self._end_tail(sess))
        self._refresh()

    def _end_tail(self, sess: Session, keep_open: bool = False) -> None:
        """Отключить микрофон от записи и только потом сказать распознавателю «конец»:
        последний аудиоблок гарантированно приходит раньше команды завершения."""
        if self._tail is not sess:
            return      # старый таймер: эта запись уже закрыта (или идёт другая) — ничего не трогаем
        self._tail = None
        self.recorder.stop(sess.sink, close=not keep_open)
        sess.asr_since = time.monotonic()
        if sess.reasr:
            self._resubmit(sess)            # распознаватель перезапускался во время записи
        else:
            self.engine.finish(sess.sid)
        if "mic" in self.notices and self.notices["mic"][0] == "error":
            self.clear_notice("mic")
        if self.rec is None:
            self.sysaudio.end()
        if self.settings.get("audio.sounds", True):
            QTimer.singleShot(60, lambda: self.sounds.play("stop"))
        self._check_clipping(sess)

    def _drop_recording(self, silent: bool = True) -> None:
        sess = self.rec
        if sess is None:
            return
        self.rec = None
        self._press_sid = None
        self.recorder.stop(sess.sink)
        self.engine.cancel(sess.sid)
        self.corrector.forget(sess.sid)
        self.sysaudio.end()
        self._after_cancel(silent)

    def _drop_jobs(self) -> None:
        for sess in list(self.jobs.values()):
            if self._tail is sess:
                self._tail = None
                self.recorder.stop(sess.sink)
            self.engine.cancel(sess.sid)
            self.corrector.forget(sess.sid)
        self.jobs.clear()
        if self.rec is None:
            self.sysaudio.end()

    def cancel(self, silent: bool = False) -> None:
        """Esc: отменить текущую запись; если записи нет — обработку предыдущих."""
        if self.rec is not None:
            self._drop_recording(silent)
        elif self.jobs:
            self._drop_jobs()
            self._after_cancel(silent)

    def cancel_all(self) -> None:
        if self.rec is not None:
            self._drop_recording(True)
        self._drop_jobs()
        self._refresh()

    def _after_cancel(self, silent: bool) -> None:
        self.bubble.set_live_text("")
        self.bubble.set_chip("")
        self._refresh()
        if not silent:
            self.bubble.show_message("Отменено", "info", 1200)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("cancel")

    def _refresh(self) -> None:
        """Облачко, трей и перехват Esc — по текущему состоянию."""
        rec = self.rec
        if rec is not None:
            state = "listening" if rec.mode == "hold" else "hands_free"
        elif self.jobs:
            state = "processing"
        else:
            state = "idle"
        self.bubble.base_state = state
        if self.bubble.state != "message" or state in ("listening", "hands_free"):
            self.bubble.set_state(state)
        if state == "idle":
            self.bubble.set_live_text("")
            self.bubble.set_chip("")
        if self.tray:
            self.tray.set_recording(rec is not None)
        self._grab_cancel(rec is not None or bool(self.jobs))

    def _check_clipping(self, sess: Session) -> None:
        usable = sess.samples - CLIP_SKIP_SAMPLES
        if not self.settings.get("audio.warn_clipping", True) or usable < 16000:
            return
        ratio = sess.clipped / max(1, usable)
        sess.info["clipped"] = ratio
        # Перегруз — это много «упёршихся» сэмплов, разбросанных по записи, а не один щелчок.
        bad = ratio >= CLIP_WARN_RATIO and sess.clip_blocks >= 6
        self._clip_history.append(bad)
        if not bad:
            return
        log.warning("Перегруз микрофона: %.1f%% сэмплов, %d блоков", ratio * 100, sess.clip_blocks)
        if sum(self._clip_history) < 2:
            return      # один раз — случайность (громкий звук рядом); ждём повторения
        device = self.settings.get("audio.input_device")

        def work():
            src = pwaudio.current_source(device)
            gain = src.gain_db if src is not None else None
            self.sig_clip.emit(ratio, -1.0 if gain is None else float(gain))
        threading.Thread(target=work, daemon=True).start()

    @Slot(float, float)
    @guarded
    def _on_clip(self, ratio: float, gain: float) -> None:
        device = self.settings.get("audio.input_device")
        if 0 <= gain <= 24:
            # Усиление и так умеренное — дело не в настройках, а в громкости рядом с микрофоном.
            log.info("Перегруз при умеренном усилении (+%.0f дБ) — уведомление не показываю", gain)
            return
        if gain > 24 and ratio >= CLIP_AUTOFIX_RATIO and time.monotonic() - self._clip_autofixed_at > 3600:
            # Сильный повторяющийся перегруз при высоком усилении — снижаем сами.
            self._clip_autofixed_at = time.monotonic()

            def work():
                before = pwaudio.current_source(device)
                ok, message = pwaudio.fix_gain(device, ratio)
                self._gain_before = before
                self.sig_notice_done.emit("clip-auto", ok, message)
            threading.Thread(target=work, daemon=True).start()
            return
        self.set_notice("clip", "warn",
                        f"Микрофон перегружен: {ratio * 100:.0f}% звука «упирается в потолок», "
                        f"из-за этого хуже распознаётся речь. Можно снизить усиление автоматически.",
                        "Снизить усиление")
        if time.monotonic() - self._clip_warned_at > 600 and self.rec is None:
            self._clip_warned_at = time.monotonic()
            QTimer.singleShot(1800, lambda: self.rec is None and self.bubble.show_message(
                "Микрофон перегружен — снизьте усиление (см. главное окно)", "warn", 4000))

    # ================================================================ результаты
    @Slot(str, str)
    @guarded
    def _on_status(self, state: str, message: str) -> None:
        self.engine_state = (state, message)
        self.bubble.set_model_loading(state in ("loading", "downloading"))
        if state == "error":
            # Сами пробуем ещё раз (через 5, 20, 60 с); человеку сообщаем, только если не вышло.
            if self._load_retries < 3:
                delay = (5, 20, 60)[self._load_retries]
                self._load_retries += 1
                log.warning("Модель не загрузилась (%s) — повтор через %d с", message, delay)
                QTimer.singleShot(delay * 1000, self.engine.reload)
            else:
                self.set_notice("engine", "error", "Распознавание не запускается: " + message +
                                " Aqua продолжит попытки; подробности — в журнале.", "Попробовать снова")
        elif state in ("ready", "degraded"):
            self._load_retries = 0
            self.clear_notice("engine")
        if state == "degraded":
            self.set_notice("vram", "warn", message + " Распознавание временно работает на процессоре "
                            "(медленнее). Закройте эти программы или нажмите кнопку — модели Ollama "
                            "выгрузятся, и распознавание вернётся на видеокарту.", "Освободить видеопамять")
            self.bubble.show_message("Видеопамять занята — распознаю на процессоре", "warn", 5000)
        elif state == "ready":
            self.clear_notice("vram")
        if state in ("ready", "degraded"):
            QTimer.singleShot(500, self._maybe_start_llm_once)
        self.status_changed.emit(state, message)
        if self.tray:
            self.tray.set_status("ready" if state == "degraded" else state, message)

    @Slot(int, str)
    @guarded
    def _on_partial(self, sid: int, text: str) -> None:
        if sid == self._live_sid and (self.rec is not None and self.rec.sid == sid or sid in self.jobs):
            self.bubble.set_live_text(text)

    @Slot(int, str, object)
    @guarded
    def _on_final(self, sid: int, text: str, info: dict) -> None:
        sess = self.jobs.get(sid)
        if sess is None or sess.raw or sess.text is not None:
            return
        info = info or {}
        if info.get("error"):
            # Сбой распознавания: звук у нас есть — перезапускаем распознаватель и пробуем ещё раз.
            if sess.asr_retries < 2 and sess.audio:
                log.warning("Сбой распознавания (%s) — распознаю запись заново", info.get("error"))
                self._restart_engine(f"сбой распознавания: {info.get('error')}")
                return
            self._keep_unrecognized(sess)
            return
        sess.info.update(info)
        if not text.strip():
            self._skip(sess)
            if self.rec is None and float(info.get("duration", 0)) >= MIN_SPEECH_REPORT_S \
                    and sess.mode == "hold":
                self.bubble.show_message("Речь не распознана", "warn", 1800)
            return
        sess.raw = text
        if sess.kind != "edit" and self.corrector.active():
            # Сначала словарь и встроенные термины (без ИИ), потом ОДИН запрос к ИИ на всю
            # диктовку — модель видит весь контекст и не «склеивает» куски.
            pre, terms = self.processor.pre(text, self.settings)
            sess.pre = pre
            sess.ai_since = time.monotonic()
            if sid == self._live_sid and self.rec is None:
                self.bubble.set_live_text("✨ " + pre)

            def work():
                try:
                    out = self.corrector.correct(pre, terms)
                except Exception:  # noqa: BLE001
                    log.exception("ИИ-исправление упало — вставляю без него")
                    out = pre
                self.sig_corrected.emit(sid, out)

            threading.Thread(target=work, name="correct", daemon=True).start()
        else:
            self._after_correction(sess, text)

    @Slot(int, str)
    @guarded
    def _on_corrected(self, sid: int, text: str) -> None:
        sess = self.jobs.get(sid)
        if sess is None or sess.text is not None:
            return
        sess.ai = text.strip() != (getattr(sess, "pre", "") or sess.raw or "").strip()
        sess.ai_since = None
        self._after_correction(sess, text)

    def _after_correction(self, sess: Session, text: str) -> None:
        processed = self.processor.process(text, self.settings)
        send = False
        if sess.mode == "hands_free" and self.settings.get("insert.send_it", True):
            processed, send = split_send_it(processed)
        if self.settings.get("text.casual_messaging", False) and sess.kind != "edit" \
                and is_messenger(f"{sess.window.wm_class} {getattr(sess.window, 'instance', '')}",
                                 sess.window.title):
            processed = casual(processed)
        sess.send = send
        if processed and sess.raw[:1].islower() and self.desktop.paste_keys_for(sess.window)[0] == \
                ["Control_L", "Shift_L"]:
            processed = processed[0].lower() + processed[1:]   # в терминале команду с заглавной не начинаем
        if sess.kind == "edit" and self.settings.get("llm.enabled", False) and self.router.available():
            self._run_llm(sess, processed)
        else:
            self._ready(sess, processed)

    # ================================================================ самовосстановление
    def _new_engine(self) -> ASREngine:
        return ASREngine(self.settings, self.sig_status.emit, self.sig_partial.emit, self.sig_final.emit)

    def _restart_engine(self, reason: str) -> None:
        """Распознаватель упал или завис: новый экземпляр, а записи распознаём заново из сохранённого звука."""
        log.warning("Перезапускаю распознавание: %s", reason)
        old = self.engine
        try:
            old.shutdown()
            old._thread.join(3)        # дать старому освободить видеопамять
        except Exception:  # noqa: BLE001
            pass
        self.engine = self._new_engine()
        self.engine.start()
        if self.rec is not None:
            self.rec.reasr = True           # идущая запись будет распознана целиком после остановки
        for sess in list(self.jobs.values()):
            if not sess.raw and sess.text is None and sess is not self._tail:
                self._resubmit(sess)
            elif sess is self._tail:
                sess.reasr = True

    def _resubmit(self, sess: Session) -> None:
        if not sess.audio:
            self._skip(sess)
            return
        sess.asr_retries += 1
        sess.asr_since = time.monotonic()
        audio = np.concatenate(sess.audio)
        sid = sess.sid
        self.engine.transcribe_array(audio, lambda text: self.sig_reasr.emit(sid, text or ""))

    @Slot(int, str)
    @guarded
    def _on_reasr(self, sid: int, text: str) -> None:
        sess = self.jobs.get(sid)
        if sess is None or sess.raw or sess.text is not None:
            return
        audio = np.concatenate(sess.audio) if sess.audio else None
        self._on_final(sid, text, {"duration": sess.samples / 16000, "latency": 0.0, "audio": audio,
                                   "chunks": [text] if text else []})

    def _keep_unrecognized(self, sess: Session) -> None:
        """Распознать так и не вышло: звук сохраняем в историю, чтобы ничего не пропало."""
        try:
            if sess.audio and not self.settings.get("general.privacy_mode", False):
                path = save_wav(np.concatenate(sess.audio))
                self.history.add("⚠ Запись не распознана — её можно прослушать и повторить", "",
                                 sess.window.app, sess.window.title, "dictation", sess.samples / 16000, 0, 0.0,
                                 path)
                self.history_changed.emit()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось сохранить нераспознанную запись")
        self._skip(sess)

    def _heal(self, where: str, args: tuple) -> None:
        """После сбоя в обработчике: не оставлять «зависших» фраз, очередь вставки и облачко — в порядке."""
        if args and isinstance(args[0], int) and args[0] in self.jobs:
            sess = self.jobs[args[0]]
            if sess.text is None:
                base = getattr(sess, "pre", "") or sess.raw
                try:
                    sess.text = self.processor.process(base, self.settings) if base else ""
                except Exception:  # noqa: BLE001
                    sess.text = base or ""
                sess.skip = not sess.text
                self._flush()
        if where in ("_pump_insert", "_paste", "_typed", "_insert_done"):
            self._insert_busy = False
            QTimer.singleShot(200, self._pump_insert)
        self._refresh()

    def _watchdog(self) -> None:
        """Раз в 2 с: всё ли живо. Если нет — чиним сами, без участия человека."""
        try:
            now = time.monotonic()
            # Распознаватель: поток умер или запись ждёт слишком долго (завис на видеокарте).
            self._check_resume()
            if now - getattr(self, "_power_checked", 0.0) > 30:
                self._power_checked = now
                threading.Thread(target=self._refresh_power, name="power", daemon=True).start()
            loading = self.engine.state in ("loading", "downloading")
            busy = getattr(self.engine, "busy_since", None)
            waiting = [s for s in self.jobs.values() if not s.raw and s.text is None and s.asr_since is not None]
            if not self.engine.alive():
                self._restart_engine("поток распознавания остановился")
            elif not loading and busy is not None and now - busy > 120:
                # Одна операция дольше 2 минут — это зависание (видеокарта, драйвер), а не медленный
                # процессор: даже 18-секундный кусок на слабом CPU распознаётся быстрее.
                stuck = [s for s in waiting if s.asr_retries >= 2]
                for sess in stuck:
                    self._keep_unrecognized(sess)
                self._restart_engine(f"распознавание не отвечает {now - busy:.0f} с")
            elif not loading and busy is None and waiting:
                # Распознаватель свободен, а запись всё ещё ждёт — команда потерялась: отправляем заново.
                for sess in waiting:
                    if now - sess.asr_since > 20:
                        log.warning("Запись #%d потерялась в распознавателе — отправляю заново", sess.sid)
                        if sess.asr_retries >= 2:
                            self._keep_unrecognized(sess)
                        else:
                            self._resubmit(sess)
            # ИИ не вернул ответ далеко за сроком — вставляем без него.
            for sess in list(self.jobs.values()):
                if sess.ai_since is not None and sess.text is None:
                    budget = self.corrector._budget_ms(len(getattr(sess, "pre", "") or ""))[0] / 1000
                    if now - sess.ai_since > budget + 10:
                        log.warning("ИИ не ответил за %.0f с — вставляю без исправления", now - sess.ai_since)
                        sess.ai_since = None
                        self._after_correction(sess, getattr(sess, "pre", "") or sess.raw)
            # Вставка «застряла» (окно не ответило, X-сервер подвис).
            if self._insert_busy and now - self._insert_started > 8:
                log.warning("Вставка не завершилась — сбрасываю очередь вставки")
                self._insert_busy = False
                self._pump_insert()
            # Клавиатура: поток XRecord умер (например, после смены раскладки или сбоя X).
            if self.listener is not None and not self.listener.alive():
                self._restart_listener()
            # Микрофон открыт, а он никому не нужен (утечка, сбой) — закрываем: значок гаснет.
            if self.rec is None and self._tail is None and not self._mic_test and hasattr(self.recorder, "close_if_unused"):
                if self.recorder.close_if_unused():
                    log.warning("Микрофон оставался открытым без записи — закрыт")
            # Проверка микрофона в окне — не дольше минуты и только пока окно на экране.
            if self._mic_test and (now - self._mic_test_since > 60 or self.window is None
                                   or not self.window.isVisible()):
                self.mic_test(False)
            # «Тёплый» микрофон в экономном режиме не держим.
            if getattr(self.recorder, "warm", False) and perf.economy(self.settings) and self.rec is None \
                    and self._tail is None:
                self.recorder.set_warm(False)
            # Микрофон замолчал посреди записи (отключили USB, PipeWire перезапустился).
            rec = self.rec
            if rec is not None and now - rec.started > 1.5 and now - rec.last_block > 2.0 \
                    and hasattr(self.recorder, "reopen"):
                log.warning("Микрофон замолчал — переподключаю")
                rec.last_block = now
                if not self.recorder.reopen(self.settings.get("audio.input_device")):
                    self.recorder.reopen(None)
                self.bubble.show_message("Микрофон переподключён — продолжайте", "info", 1500)
        except Exception:  # noqa: BLE001
            log.exception("Сбой сторожа")

    def _refresh_power(self) -> None:
        if perf.refresh_power():
            log.info("%s", perf.detect().label())
            self.sig_power.emit()

    def _on_power(self) -> None:
        self.bubble.update_frame_rate()
        if perf.economy(self.settings) and getattr(self.recorder, "warm", False) and self.rec is None:
            self.recorder.set_warm(False)

    def _check_resume(self) -> None:
        """Ноутбук проснулся (часы ушли вперёд, а монотонное время стояло): чиним то, что
        обычно ломает сон, — соединения, микрофон, видеокарту."""
        wall, mono = time.time(), time.monotonic()
        last = getattr(self, "_clock", None)
        self._clock = (wall, mono)
        if last is None:
            return
        slept = (wall - last[0]) - (mono - last[1])
        if slept < 20:
            return
        log.info("Компьютер просыпался (сон ~%.0f с) — проверяю устройства", slept)
        llm.reset_connections()
        pwaudio.invalidate()
        perf.detect(force=True)
        self.bubble.update_frame_rate()
        if self.rec is not None:
            if hasattr(self.recorder, "reopen") and not self.recorder.reopen(self.settings.get("audio.input_device")):
                self.recorder.reopen(None)
        elif getattr(self.recorder, "warm", False):
            self.recorder.set_warm(False)
            if self.settings.get("audio.keep_mic_warm") and not perf.economy(self.settings):
                self.recorder.set_warm(True, self.settings.get("audio.input_device"))
        # После сна драйвер NVIDIA иногда теряет контекст CUDA: проверяем распознаватель коротким
        # тестом, при ошибке он сам перезапустится (и при необходимости уйдёт на процессор).
        self.engine.health_check(lambda ok: None if ok else self.sig_heal_engine.emit("сбой после сна"))
        self.clear_notice("clip")

    def _restart_listener(self) -> None:
        log.warning("Перезапускаю перехват клавиш")
        try:
            if self.listener is not None:
                self.listener.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.listener = X11KeyListener(self.sig_key.emit)
            self.listener.start()
            self.grabber = X11Grabber()
            self._cancel_grabbed = False
            self._grab_static()
            self.keys = KeyState()
            self._act_down = False
        except Exception:  # noqa: BLE001
            log.exception("Клавиши пока недоступны — попробую позже")

    def _skip(self, sess: Session) -> None:
        sess.skip = True
        sess.text = ""
        self._flush()

    def _ready(self, sess: Session, text: str) -> None:
        sess.text = text
        self._flush()

    @guarded
    def _flush(self) -> None:
        """Отдать готовые результаты строго по порядку записей."""
        while self.jobs:
            sid, sess = next(iter(self.jobs.items()))
            if sess.text is None:
                break
            del self.jobs[sid]
            if not sess.skip:
                self._deliver(sess, sess.text)
        if self._live_sid is not None and self._live_sid not in self.jobs and \
                (self.rec is None or self.rec.sid != self._live_sid):
            self._live_sid = None
            self.bubble.set_live_text("")
            self.bubble.set_chip("")
        self._refresh()

    # ---------------------------------------------------------------- ИИ
    def _maybe_start_llm_once(self) -> None:
        if not self._llm_started_once:
            self._llm_started_once = True
            self._maybe_start_llm()

    def _maybe_start_llm(self) -> None:
        wanted = self.settings.get("llm.correct", False) or self.settings.get("llm.enabled", False)
        local_kind = self.router.local_kind()
        cloud = self.router.cloud() is not None
        if cloud and wanted:
            # Основной — DeepSeek: локальную модель в видеопамять не грузим, она поднимется
            # сама, если DeepSeek перестанет отвечать.
            self.local_llm.stop()
            self.llm_state_changed.emit("cloud", "DeepSeek", -1)
            return
        if local_kind != "builtin":
            self.local_llm.stop()
            if wanted:
                # Ollama: загрузить модель в память заранее (keep_alive -1) и прогреть кэш промпта.
                threading.Thread(target=self.corrector.warmup, name="llm-warmup", daemon=True).start()
                self.llm_state_changed.emit("ready" if local_kind == "ollama" else "external",
                                            f"Ollama · {self.settings.get('llm.ollama_model')}"
                                            if local_kind == "ollama" else "свой сервер", -1)
            return
        if wanted and self.local_llm.installed():
            self.local_llm.start_async()
        elif not wanted:
            self.local_llm.stop()

    def install_llm(self) -> None:
        """Скачать (если нужно) и запустить встроенную модель — из настроек."""
        self.local_llm.start_async()

    def _start_local_fallback(self) -> None:
        if self.router.local_kind() == "builtin" and self.local_llm.installed() and not self.local_llm.ready:
            log.info("DeepSeek недоступен — запускаю запасную локальную модель")
            self.local_llm.start_async()

    @Slot(str, bool)
    def _on_cloud_failed(self, reason: str, fatal: bool) -> None:
        local = self.router.local() is not None or self.router.local_configured()
        tail = " — пока работает запасная локальная модель" if local else " — текст вставляется без исправления"
        self.set_notice("cloud", "error" if fatal else "warn", f"ИИ: {reason}{tail}.",
                        "Настройки ИИ" if fatal else "")
        if self.rec is None:
            self.bubble.show_message(f"{reason[:1].upper()}{reason[1:]}"[:60], "warn", 3000)

    @Slot(str, str, float)
    def _on_llm_state(self, state: str, message: str, progress: float) -> None:
        self.llm_state_changed.emit(state, message, progress)
        if state == "ready":
            threading.Thread(target=self.corrector.warmup, name="llm-warmup", daemon=True).start()
        if state == "error" and self.router.cloud() is None:
            self.bubble.show_message("ИИ не запустился — текст вставляется без исправления", "warn", 4000)

    def _run_llm(self, sess: Session, command: str) -> None:
        if sess.sid == self._live_sid:
            self.bubble.set_live_text("✨ Правлю выделенный текст…")
        settings, router = self.settings, self.router

        def work():
            try:
                result = llm.run_command(settings, router, command, sess.selection, sess.window.app)
                self.sig_llm.emit(sess.sid, result, "")
            except Exception as exc:  # noqa: BLE001
                log.warning("ИИ-обработка не удалась: %s", exc)
                self.sig_llm.emit(sess.sid, "", str(exc))

        threading.Thread(target=work, daemon=True).start()

    @Slot(int, str, str)
    @guarded
    def _on_llm(self, sid: int, text: str, error: str) -> None:
        sess = self.jobs.get(sid)
        if sess is None:
            return
        if error or not text.strip():
            # Не вставляем команду вместо выделенного текста — выделение остаётся как было.
            if self.rec is None:
                self.bubble.show_message("ИИ недоступен — текст не изменён", "warn", 2500)
            self._skip(sess)
            return
        sess.ai = True
        self._ready(sess, text)

    def _deliver(self, sess: Session, text: str) -> None:
        info = sess.info or {}
        raw = sess.raw or text
        send = sess.send
        if text and self.settings.get("insert.trailing_space", True) and not send and sess.kind != "edit" \
                and not text.endswith((" ", "\n")):
            text += " "
        last = self._last_insert
        if text and sess.kind != "edit" and last and last[0] == (sess.window.wm_class, sess.window.title) \
                and time.monotonic() - last[2] < 120 and last[1] and not last[1][-1].isspace() \
                and text[0] not in ",.!?…:;)":
            text = " " + text   # предыдущая вставка в это окно закончилась без пробела
        if text:
            self._last_text = text.lstrip(" ")
            # После «Отправь» поле ввода пустое — следующей фразе пробел в начале не нужен.
            self._last_insert = None if send else ((sess.window.wm_class, sess.window.title), text,
                                                    time.monotonic())
            self.insert_text(text, send=send)
        elif send:
            self.insert_text("", send=True)
        # Пустые записи (тишина, отменённые) в историю не попадают.
        if not self.settings.get("general.privacy_mode", False) and text.strip():
            audio_path = None
            audio = info.get("audio")
            if self.settings.get("audio.save_audio", True) and audio is not None and len(audio):
                try:
                    audio_path = save_wav(audio)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось сохранить аудио")
            mode = {"edit": "edit"}.get(sess.kind, "hands-free" if sess.mode == "hands_free" else "dictation")
            if sess.ai and sess.kind != "edit":
                mode += "+ai"
            self.history.add(text.strip(), raw, sess.window.app, sess.window.title, mode,
                             float(info.get("duration", 0.0)), count_words(text),
                             float(info.get("latency", 0.0)), audio_path)
            self.history_changed.emit()

    # ================================================================ вставка
    def paste_last(self) -> None:
        text = self._last_text
        if not text:
            row = self.history.last()
            text = row["text"] if row else ""
            if text and self.settings.get("insert.trailing_space", True):
                text += " "
        if not text:
            self.bubble.show_message("История пуста", "info", 1500)
            return
        self.insert_text(text)

    def insert_text(self, text: str, send: bool = False) -> None:
        """Поставить текст в очередь вставки: по одному, по порядку, без гонок за буфер обмена."""
        self._outbox.append((text, send))
        self._pump_insert()

    @guarded
    def _pump_insert(self) -> None:
        if self._insert_busy or not self._outbox:
            return
        if self.keys.any_modifier():
            # Человек держит Alt (новая диктовка) или Ctrl — Ctrl+V превратился бы в Alt+Ctrl+V.
            # Ждём отпускания. Модификаторы НЕ отпускаем за человека: фальшивое отпускание
            # Alt оборвало бы новую запись.
            now = time.monotonic()
            if self._insert_wait_since is None:
                self._insert_wait_since = now
            if self.rec is None and not self._act_down and now - self._insert_wait_since > 8:
                text, _send = self._outbox.popleft()
                self._insert_wait_since = None
                self._set_clipboard(text, hide_from_history=False)
                self.bubble.show_message("Текст в буфере — вставьте Ctrl+V", "warn", 3000)
                QTimer.singleShot(0, self._pump_insert)
                return
            QTimer.singleShot(25, self._pump_insert)
            return
        self._insert_wait_since = None
        text, send = self._outbox.popleft()
        self._insert_busy = True
        self._insert_started = time.monotonic()
        if not text:
            self._press_send()
            QTimer.singleShot(60, self._insert_done)
            return
        method = self.settings.get("insert.method", "paste")
        if method == "type":
            def work():
                ok = xdotool_type(text)
                QTimer.singleShot(0, lambda: self._typed(ok, text, send))
            threading.Thread(target=work, daemon=True).start()
            return
        if method == "clipboard":
            self._set_clipboard(text, hide_from_history=False)
            self.bubble.show_message("Скопировано в буфер обмена", "info", 1500)
            QTimer.singleShot(0, self._insert_done)
            return
        self._paste(text, send)

    @guarded
    def _typed(self, ok: bool, text: str, send: bool) -> None:
        if not ok:
            self._paste(text, send)
            return
        if send:
            self._press_send()
        self._insert_done()

    @guarded
    def _insert_done(self) -> None:
        self._insert_busy = False
        if self._outbox:
            self._pump_insert()
        elif self._clip_restore is not None or self._clip_text:
            self._restore_clipboard()

    def _snapshot_clipboard(self) -> Optional[QMimeData]:
        clip = QGuiApplication.clipboard()
        md = clip.mimeData(QClipboard.Clipboard)
        if md is None:
            return None
        snap = QMimeData()
        try:
            for fmt in md.formats():
                if fmt.startswith(SAFE_MIME):
                    snap.setData(fmt, md.data(fmt))
        except Exception:  # noqa: BLE001
            return None
        return snap

    def _set_clipboard(self, text: str, hide_from_history: bool) -> None:
        md = QMimeData()
        md.setText(text)
        if hide_from_history:
            # Подсказка менеджерам буфера (KDE Klipper, GPaste, CopyQ) не запоминать.
            md.setData("x-kde-passwordManagerHint", b"secret")
        QGuiApplication.clipboard().setMimeData(md, QClipboard.Clipboard)

    @guarded
    def _paste(self, text: str, send: bool) -> None:
        restore = self.settings.get("insert.restore_clipboard", True)
        # Снимок буфера — один раз на серию вставок; восстанавливаем после последней.
        if restore and self._clip_restore is None and not self._clip_text:
            self._clip_restore = self._snapshot_clipboard()
        self._clip_text = text if restore else ""
        self._set_clipboard(text, hide_from_history=restore)

        def keys():
            window = self.desktop.active_window()
            mods, key = self.desktop.paste_keys_for(window, self.settings.get("insert.terminal_shift_paste", True))
            if not self.desktop.send_combo(mods, key):
                self.bubble.show_message("Не удалось вставить — текст в буфере", "warn", 2500)
            if send:
                QTimer.singleShot(140, self._press_send)

        QTimer.singleShot(30, keys)
        # Приложение забирает текст из буфера не мгновенно: следующую вставку (и возврат
        # старого буфера) делаем только после паузы.
        QTimer.singleShot(int(self.settings.get("insert.restore_delay_ms", 450)) + (160 if send else 0),
                          self._insert_done)

    def _restore_clipboard(self) -> None:
        snap, self._clip_restore = self._clip_restore, None
        ours, self._clip_text = self._clip_text, ""
        if not ours:
            return
        clip = QGuiApplication.clipboard()
        if clip.text(QClipboard.Clipboard) != ours:
            return  # человек уже скопировал что-то своё
        if snap is not None and snap.formats():
            clip.setMimeData(snap, QClipboard.Clipboard)
        else:
            clip.clear(QClipboard.Clipboard)

    def _press_send(self) -> None:
        key = self.settings.get("insert.send_key", "enter")
        if key == "enter":
            self.desktop.send_combo([], "Return")
        elif key == "ctrl+enter":
            self.desktop.send_combo(["Control_L"], "Return")

    # ================================================================ уведомления
    def set_notice(self, key: str, kind: str, text: str, action: str = "") -> None:
        if self.notices.get(key) == (kind, text, action):
            return
        self.notices[key] = (kind, text, action)
        self.notices_changed.emit()

    def clear_notice(self, key: str) -> None:
        if self.notices.pop(key, None) is not None:
            self.notices_changed.emit()

    def notice_action(self, key: str) -> None:
        """Кнопка в уведомлении на главной странице."""
        if key == "clip" and self.notices.get("clip", ("", "", ""))[2] == "Вернуть как было":
            before = getattr(self, "_gain_before", None)

            def undo():
                ok = pwaudio.restore_volume(before)
                self.sig_notice_done.emit("clip", ok, "Усиление микрофона возвращено." if ok else
                                          "Не удалось вернуть усиление.")
            threading.Thread(target=undo, daemon=True).start()
            return
        if key == "engine":
            self._load_retries = 0
            self.engine.retry_gpu()
            self.clear_notice("engine")
            return
        if key == "mic":
            self.clear_notice("mic")
            self.show_window("settings")
            return
        if key == "clip":
            device = self.settings.get("audio.input_device")

            def work():
                ok, message = pwaudio.fix_gain(device)
                self.sig_notice_done.emit("clip", ok, message)
            threading.Thread(target=work, daemon=True).start()
        elif key == "vram":
            def work():
                from .corrector import ollama_unload
                keep = ""
                if self.router.cloud() is None and self.router.local_kind() == "ollama":
                    keep = self.settings.get("llm.ollama_model") or ""
                names = ollama_unload(self.settings.get("llm.ollama_url") or "http://127.0.0.1:11434", keep)
                time.sleep(1.0)
                self.engine.retry_gpu()
                msg = ("Выгружено из Ollama: " + ", ".join(names) + ". Возвращаю распознавание на видеокарту…"
                       if names else "Пробую снова загрузить распознавание на видеокарту…")
                self.sig_notice_done.emit("vram", True, msg)
            threading.Thread(target=work, daemon=True).start()
        elif key == "cloud":
            self.router.reset_cloud()
            self.show_window("settings")
            if self.window is not None and hasattr(self.window, "focus_ai"):
                self.window.focus_ai()

    @Slot(str, bool, str)
    def _on_notice_done(self, key: str, ok: bool, message: str) -> None:
        if key == "clip-auto":
            if ok:
                self.set_notice("clip", "info", "Микрофон сильно перегружался — Aqua сама снизила усиление. "
                                + message.split(".")[0] + ".", "Вернуть как было")
            else:
                self.set_notice("clip", "warn", "Микрофон перегружен, а снизить усиление автоматически не "
                                "получилось. " + message, "")
            return
        if key == "clip":
            self.set_notice("clip", "info" if ok else "error", message, "")
            if ok:
                QTimer.singleShot(15000, lambda: self.clear_notice("clip"))
        elif key == "vram":
            self.set_notice("vram", "info", message, "")

    # ================================================================ разное
    def _on_setting(self, key: str, value) -> None:
        if key.startswith("hotkeys."):
            self._grab_static()
            self.bubble.set_hint(self.hint_text())
        elif key.startswith("bubble."):
            self.bubble.apply_targets()
            self.bubble.reposition()
            self.bubble._kick()
        elif key == "audio.keep_mic_warm" or key == "audio.input_device":
            if self.rec is None and self._tail is None:
                self.recorder.set_warm(False)
                if self.settings.get("audio.keep_mic_warm"):
                    self.recorder.set_warm(True, self.settings.get("audio.input_device"))
            if key == "audio.input_device":
                self.clear_notice("clip")
        elif key in ("asr.device", "asr.precision", "asr.model_dir", "asr.cpu_threads"):
            self.engine.force_cpu_reason = ""
            self.engine.reload()
        elif key in ("llm.correct", "llm.enabled", "llm.provider", "llm.ollama_model", "llm.ollama_url",
                     "llm.deepseek_key", "llm.cloud"):
            if key in ("llm.deepseek_key", "llm.cloud"):
                self.router.reset_cloud()
                self.clear_notice("cloud")
            QTimer.singleShot(0, self._maybe_start_llm)
        elif key == "llm.builtin_backend":
            self.local_llm.stop()
            QTimer.singleShot(0, self._maybe_start_llm)

    def set_ai(self, on: bool) -> None:
        """Включить/выключить улучшение текста ИИ (трей, главная страница)."""
        self.settings.set("llm.correct", bool(on))
        if on and not self.router.available() and not self.router.local_configured():
            self.bubble.show_message("ИИ не настроен — вставьте ключ DeepSeek в Настройках", "warn", 4000)
            self.show_window("settings")
            if self.window is not None and hasattr(self.window, "focus_ai"):
                self.window.focus_ai()

    def set_polish(self, on: bool) -> None:
        """Режим «Улучшать структуру и стиль» (трей, настройки)."""
        self.settings.set("llm.style", "polish" if on else "fix")
        if self.rec is None:
            self.bubble.show_message("ИИ: улучшает структуру и стиль" if on else "ИИ: только исправляет ошибки",
                                     "info", 1600)

    def set_microphone(self, value) -> None:
        self.settings.set("audio.input_device", value)
        from .audio import device_label
        if self.rec is None:
            self.bubble.show_message(f"Микрофон: {device_label(value)}"[:60], "info", 1800)

    def begin_hotkey_capture(self) -> None:
        self.hotkey_recorder.begin(lambda combo: self.hotkey_captured.emit(list(combo), False),
                                   lambda combo: self.hotkey_captured.emit(list(combo), True))

    def end_hotkey_capture(self) -> None:
        self.hotkey_recorder.active = False

    def mic_test(self, on: bool) -> None:
        """Проверка микрофона на главной странице (без распознавания)."""
        if self.rec is not None:
            return
        if on and not self._mic_test:
            try:
                self.recorder.start(self.settings.get("audio.input_device"), self._mic_sink, preroll_blocks=0)
                self._mic_test = True
                self._mic_test_since = time.monotonic()
            except Exception as exc:  # noqa: BLE001
                self.status_changed.emit("mic-error", str(exc))
        elif not on and self._mic_test:
            self._mic_test = False
            self.recorder.stop(self._mic_sink)

    def show_window(self, page: str = "home") -> None:
        if self.window is None:
            from .ui.main_window import MainWindow
            self.window = MainWindow(self)
        self.window.open_page(page)

    def rebuild_window(self) -> None:
        """Пересоздать окно (смена темы или режима «стекло»), сохранив страницу и геометрию."""
        old = self.window
        if old is None:
            return
        page = old.current_page()
        geometry = old.saveGeometry()
        old.detach()
        old.hide()
        old.deleteLater()
        self.window = None
        self.show_window(page)
        self.window.restoreGeometry(geometry)

    def _bubble_menu(self, pos) -> None:
        from .ui.tray import build_menu
        menu = build_menu(self, parent=None)
        menu.exec(pos)

    def retranscribe(self, row_id: int, callback) -> None:
        """Повторно распознать сохранённое аудио из истории; callback(text|None) в главном потоке."""
        from .storage import load_wav
        row = self.history.get(row_id)
        if not row or not row["audio"] or not os.path.exists(row["audio"]):
            callback(None)
            return
        self._retranscribe_callbacks[row_id] = callback
        audio = load_wav(row["audio"])
        self.engine.transcribe_array(audio, lambda text: self.sig_retranscribed.emit(row_id, text or ""))

    @Slot(int, str)
    def _on_retranscribed(self, row_id: int, text: str) -> None:
        callback = self._retranscribe_callbacks.pop(row_id, None)
        processed = self.processor.process(text, self.settings) if text else ""
        if processed:
            self.history.update_text(row_id, processed, count_words(processed))
            self.history_changed.emit()
        if callback:
            callback(processed)


def plural_words(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "слово"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "слова"
    return "слов"


def send_ipc(command: str, timeout: float = 1.5) -> bool:
    """Отправить команду запущенному экземпляру. False — приложение не запущено."""
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(IPC_PATH)
            sock.sendall((command + "\n").encode("utf-8"))
            sock.recv(16)
        return True
    except OSError:
        return False
