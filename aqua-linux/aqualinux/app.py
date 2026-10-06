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
import re
import shutil
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from PySide6.QtCore import QByteArray, QMimeData, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from . import llm, perf, pwaudio, xclip
from .asr.engine import ASREngine
from . import audio as audio_backend
from .audio import Recorder, SoundPlayer, clipped_count, friendly_mic_error
from .config import APP_ID, Settings
from .corrector import Corrector
from .hotkeys import (MODIFIER_GENERIC, MODIFIER_TOKENS, HotkeyRecorder, KeyState, SelectionTracker,
                      X11Grabber, X11KeyListener, pretty_combo, read_x_selection)
from .inserter import X11Desktop, WindowInfo, xdotool_type
from .localllm import LocalLLM
from .postprocess import TextProcessor, casual, count_words, is_messenger, split_send_it
from .storage import (DEFAULT_DICTIONARY, DEFAULT_REPLACEMENTS, History, JsonStore, migrate_dictionary,
                      sanitize_stores)
from .sysaudio import SystemAudio
from .sysevents import SystemEvents

log = logging.getLogger(__name__)

IPC_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"{APP_ID}-{os.getuid()}.sock")
CLIP_WARN_RATIO = 0.01     # больше 1 % сэмплов в потолок — микрофон перегружен
CLIP_AUTOFIX_RATIO = 0.05  # больше 5 % — снижаем усиление сами (один раз, с кнопкой «Вернуть»)
CLIP_SKIP_SAMPLES = 5600   # первые 0,35 с записи — щелчок при включении микрофона не считаем
MIN_SPEECH_REPORT_S = 0.8  # «Речь не распознана» показываем только для записей длиннее
LOW_DISK_MB = 300          # меньше — не сохраняем звук записей
AUDIO_HANG_S = 6.0         # операция с микрофоном дольше — звуковая система зависла
JOB_MAX_S = 300            # фраза обрабатывается дольше — что-то сломалось: звук в историю, облачко свободно
LOADING_JOB_MAX_S = 1800   # пока модель грузится (первый запуск, медленный интернет) ждём дольше
MIC_TEST_SID = -1
SILENT_PEAK = 1e-4         # тише — не речь и не шум, а «цифровой ноль»: микрофон выключен
KEYSYNC_MS = 500
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
        self.peak = 0.0                   # самый громкий сэмпл (0 — микрофон выключен в системе)


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
    sig_disk = Signal(int)
    sig_mic_result = Signal(int, bool, bool, str)
    sig_typed = Signal(bool, str, bool)
    sig_snapshot = Signal(object, int)
    sig_prefetch = Signal(object, int)
    sig_muted = Signal(int)            # 1 — выключен в системе, 0 — не выключен, но молчит
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
        self.history = History(on_change=self.history_changed.emit)
        self.dictionary = JsonStore("dictionary.json", DEFAULT_DICTIONARY)
        self.replacements = JsonStore("replacements.json", DEFAULT_REPLACEMENTS)
        sanitize_stores(self.dictionary, self.replacements)
        migrate_dictionary(self.dictionary)
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
        self._outbox: deque = deque()               # очередь вставки (text, send, окно)
        self._held_by_lock = False                  # в очереди есть текст, отложенный блокировкой экрана
        self._resumed_at = 0.0
        self._insert_busy = False
        self._insert_wait_since: Optional[float] = None
        self._clip_restore: Optional[dict] = None   # снимок буфера {MIME: bytes} до серии вставок
        self._clip_snapped = False
        self._snap_seq = 0
        self._snap_pending = None
        self._prefetched = None                      # (снимок, поколение буфера, когда)
        self._prefetching = False
        self._clip_gen = 0                           # растёт при каждой смене содержимого буфера
        self._clip_text = ""
        self._clip_warned_at = 0.0
        self._llm_started_once = False
        self._insert_started = 0.0
        self._insert_limit = 8.0
        self._load_retries = 0
        self._clip_autofixed_at = 0.0
        self._low_disk = False
        self._audio_hang = False
        self._restart_reason = ""
        self._last_key_event = 0.0
        # Сверка «залипших» клавиш — только пока что-то считается нажатым.
        self._keysync_timer = QTimer(self)
        self._keysync_timer.setInterval(KEYSYNC_MS)
        self._keysync_timer.timeout.connect(self._sync_keys)
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
        self.sig_disk.connect(self._on_disk)
        self.sig_mic_result.connect(self._on_mic_result, Qt.QueuedConnection)
        self.sig_typed.connect(self._typed)
        self.sig_muted.connect(self._on_muted)
        self.sig_snapshot.connect(self._on_snapshot)
        self.sig_prefetch.connect(self._on_prefetch)
        QGuiApplication.clipboard().dataChanged.connect(self._clip_changed)
        # Блокировка экрана и сон (D-Bus): не писать на экране блокировки, чинить всё после сна.
        self.sysevents = SystemEvents(self)
        self.sysevents.lock_changed.connect(self._on_lock)
        self.sysevents.sleep_changed.connect(self._on_sleep)
        self._retranscribe_callbacks: dict[int, object] = {}
        self.settings.on_change(self._on_setting)

        self.window = None
        self.tray = None
        self.quitting = False
        self.exit_code = 0                  # 75 — сторож должен запустить заново
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
        audio_backend.preload()      # PortAudio — в фоне: зависшая звуковая система не держит запуск
        pwaudio.prefetch()           # список микрофонов — тоже заранее и в фоне
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
        # Обслуживание раз в час: старая история и звук, место на диске, мусорные файлы.
        self._maintenance_timer = QTimer(self)
        self._maintenance_timer.timeout.connect(self._maintenance)
        self._maintenance_timer.start(3600 * 1000)
        QTimer.singleShot(5000, self._maintenance)
        if not self.history.persistent:
            self.set_notice("storage", "warn", "История не сохраняется: нет доступа к папке данных ("
                            + self.history.problem[:120] + "). Диктовка работает как обычно.", "")

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
                     lambda: self.recorder.wait_idle(1.5),
                     self.sysaudio.end,
                     lambda: self.sysaudio.wait_idle(2.0),
                     lambda: self.history.close(3.0)):
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
            audio = np.concatenate(sess.audio) if sess.audio and self.settings.get("audio.save_audio", True) \
                else None
            self.history.add(text.strip(), sess.raw or text, sess.window.app, sess.window.title, "dictation",
                             sess.samples / 16000, count_words(text), 0.0, audio)

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
        elif cmd == "restart":
            # Перезапуск по просьбе человека: сторож поднимет приложение заново, когда закончим фразы.
            if os.environ.get("AQUA_SUPERVISED") == "1":
                self._restart_reason = "по команде"
            else:
                log.warning("Перезапуск недоступен: Aqua запущена без сторожа (--no-supervisor)")
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
        self._last_key_event = time.monotonic()
        if self.keys.down and not self._keysync_timer.isActive():
            self._keysync_timer.start()
        self.selection.note_input(kind, name, self.keys)
        if self.hotkey_recorder.active:
            self.hotkey_recorder.feed(kind, name)
            return
        if not self.settings.get("hotkeys.enabled", True):
            return

        activate = self._combos("activate")
        if kind == "press" and self.sysevents.locked and self.rec is None:
            return      # экран блокировки: Правый Alt нужен для ввода пароля, не для диктовки
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

    @guarded
    def _sync_keys(self) -> None:
        """Отпускание клавиши потерялось (заблокировали экран с зажатым Alt, переключили консоль,
        X-сервер подвис) — без сверки запись шла бы до лимита, а вставка ждала бы отпускания."""
        if not self.keys.down:
            self._keysync_timer.stop()
            return
        if self.listener is None or time.monotonic() - self._last_key_event < 0.4:
            return
        state = self.listener.physically_down(list(self.keys.down))
        for name, down in state.items():
            if down is False and name in self.keys.down:
                log.warning("Отпускание клавиши %s потерялось — отпускаю сам", name)
                self._on_key("release", name, time.monotonic())

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
        if self.sysevents.locked and self.rec is None:
            return
        if self.rec is not None:
            self.finish()
        else:
            self.start("hands_free")

    # ================================================================ запись
    def start(self, mode: str, t: Optional[float] = None) -> bool:
        if self.rec is not None or self.sysevents.locked:
            return False
        if self._tail is not None:
            # Предыдущая запись: отдать распознавателю; микрофон не закрываем — он сразу нужен.
            self._end_tail(self._tail, keep_open=True)
        if self._mic_test:
            self._mic_test = False
            self.recorder.stop(self._mic_sink, close=False)
        self._sid += 1
        sess = Session(self._sid, mode, "dictation", WindowInfo())
        if t is not None:
            sess.t_down = t
        streaming = self.settings.get("asr.streaming", "hands_free")
        preview = streaming == "always" or (streaming == "hands_free" and mode == "hands_free")
        # Порядок важен: сессия в распознавателе → сессия в приложении → микрофон.
        # Тогда даже первые блоки и предзапись попадают в эту запись. Микрофон открывается
        # в фоне — облачко появляется сразу, даже если звуковая система тормозит.
        self.engine.begin(sess.sid, preview)
        sess.sink = self._make_sink(sess)
        self.rec = sess
        self._open_mic(sess)
        # Окно и выделение узнаём, пока микрофон открывается.
        sess.window = self.desktop.active_window()
        if self.settings.get("edit_mode.enabled", True) and self.selection.is_live():
            text = read_x_selection("PRIMARY")
            if text.strip() and len(text) <= int(self.settings.get("edit_mode.max_chars", 6000)):
                sess.kind, sess.selection = "edit", text
        kind, selection = sess.kind, sess.selection
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

    def _open_mic(self, sess: Session) -> None:
        """Включить микрофон (в фоне). Выбранный не открылся — сами берём системный, без вопросов."""
        sid = sess.sid

        def result(ok, fallback, error):
            self.sig_mic_result.emit(sid, bool(ok), bool(fallback), friendly_mic_error(error) if error else "")

        self.recorder.start(self.settings.get("audio.input_device"), sess.sink, on_result=result, fallback=True)

    @Slot(int, bool, bool, str)
    @guarded
    def _on_mic_result(self, sid: int, ok: bool, fallback: bool, reason: str) -> None:
        if sid == MIC_TEST_SID:
            if not ok:
                self._mic_test = False
                self.status_changed.emit("mic-error", reason)
            return
        if ok:
            if fallback:
                self.set_notice("mic", "info", "Выбранный микрофон недоступен — пишу с системного. "
                                "Когда он снова подключится, Aqua вернётся к нему сама.", "")
            return
        log.warning("Микрофон не включился: %s", reason)
        if self.rec is not None and self.rec.sid == sid:
            self.rec = None
            self._press_sid = None
            self.engine.cancel(sid)
            self.sysaudio.end()
        elif sid in self.jobs:
            sess = self.jobs[sid]
            if self._tail is sess:
                self._tail = None
            self.engine.cancel(sid)
            self._skip(sess)
            if self.rec is None:
                self.sysaudio.end()
        else:
            return
        self.bubble.set_live_text("")
        self._refresh()
        if self.settings.get("audio.sounds", True):
            self.sounds.play("error")
        self.bubble.show_message(f"Микрофон: {reason}"[:80], "error", 4000)
        self.set_notice("mic", "error", f"Не получилось включить микрофон: {reason}. Проверьте, что он "
                        "подключён и не занят другой программой — Aqua попробует снова при следующем нажатии.",
                        "Выбрать микрофон")

    def _make_sink(self, sess: Session):
        """Приёмник аудио, навсегда привязанный к своей сессии (вызывается из потока PortAudio)."""
        sid = sess.sid

        def sink(block):
            self.engine.feed(sid, block)      # движок мог быть перезапущен — берём текущий
            sess.audio.append(block)
            sess.samples += block.size
            sess.last_block = time.monotonic()
            if sess.peak < SILENT_PEAK:
                sess.peak = max(sess.peak, float(np.abs(block).max()) if block.size else 0.0)
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
        self._prefetch_clipboard()
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
        self._check_silence(sess)

    def _drop_recording(self, silent: bool = True) -> None:
        sess = self.rec
        if sess is None:
            return
        self.rec = None
        self._press_sid = None
        self.recorder.stop(sess.sink)
        self.engine.cancel(sess.sid)
        self.sysaudio.end()
        self._after_cancel(silent)

    def _drop_jobs(self) -> None:
        for sess in list(self.jobs.values()):
            if self._tail is sess:
                self._tail = None
                self.recorder.stop(sess.sink)
            self.engine.cancel(sess.sid)
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

    def _check_silence(self, sess: Session) -> None:
        """Целая секунда «цифрового нуля» — микрофон выключен (кнопкой на клавиатуре, в настройках
        звука, выключателем на гарнитуре). Говорим об этом прямо, а не «речь не распознана»."""
        if sess.samples < 16000 or sess.peak >= SILENT_PEAK:
            if "mute" in self.notices and sess.samples >= 16000:
                self.clear_notice("mute")
            return
        device = self.settings.get("audio.input_device")

        def work():
            src = pwaudio.current_source(device)
            self.sig_muted.emit(1 if src is not None and src.muted else 0)
        threading.Thread(target=work, name="mute-check", daemon=True).start()

    @Slot(int)
    @guarded
    def _on_muted(self, muted: int) -> None:
        if muted:
            self.set_notice("mute", "error", "Микрофон выключен в системе (кнопкой на клавиатуре или в "
                            "настройках звука) — Aqua слышит тишину.", "Включить микрофон")
            message = "Микрофон выключен в системе — см. главное окно"
        else:
            self.set_notice("mute", "warn", "Микрофон передаёт полную тишину. Проверьте выключатель на "
                            "гарнитуре или выберите другой микрофон.", "Выбрать микрофон")
            message = "Микрофон молчит — см. главное окно"
        if self.rec is None:
            QTimer.singleShot(400, lambda: self.rec is None and self.bubble.show_message(message, "warn", 3500))

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
                # И дальше пробуем сами — раз в 5 минут (интернет появится, диск освободится).
                QTimer.singleShot(300_000, lambda: self.engine.state == "error" and self.engine.reload())
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
        terminal = self.desktop.paste_keys_for(sess.window)[0] in (["Control_L", "Shift_L"], ["Shift_L"])
        if processed and terminal:
            # В терминале перевод строки — это Enter: многострочный текст (абзацы, списки ИИ)
            # выполнился бы по кускам. Вставляем одной строкой.
            processed = one_line(processed)
            if sess.raw[:1].islower():
                processed = processed[0].lower() + processed[1:]   # команду с заглавной не начинаем
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
                self.history.add("⚠ Запись не распознана — её можно прослушать и повторить", "",
                                 sess.window.app, sess.window.title, "dictation", sess.samples / 16000, 0, 0.0,
                                 np.concatenate(sess.audio))
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
        if where in ("_pump_insert", "_paste", "_typed", "_insert_done", "_on_snapshot"):
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
            if self._insert_busy and now - self._insert_started > self._insert_limit:
                log.warning("Вставка не завершилась — сбрасываю очередь вставки")
                self._insert_busy = False
                self._pump_insert()
            elif self._outbox and not self._insert_busy and not self.sysevents.locked:
                self._pump_insert()     # например, сигнал о разблокировке экрана потерялся
            # Клавиатура: поток XRecord умер (например, после смены раскладки или сбоя X).
            if self.listener is not None and not self.listener.alive():
                self._restart_listener()
            # Микрофон открыт, а он никому не нужен (утечка, сбой) — закрываем: значок гаснет.
            if self.rec is None and self._tail is None and not self._mic_test:
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
                    and not self.recorder.stuck_for():
                log.warning("Микрофон замолчал — переподключаю")
                rec.last_block = now
                self.recorder.reopen(self.settings.get("audio.input_device"))
                self.bubble.show_message("Микрофон переподключён — продолжайте", "info", 1500)
            # Звуковая система не отвечает (PipeWire завис): запись заканчиваем тем, что успели
            # записать, и перезапускаемся, как только освободимся, — в новом процессе звук оживает.
            stuck = self.recorder.stuck_for()
            if stuck > AUDIO_HANG_S:
                self._on_audio_hang(stuck)
            elif self._audio_hang and not stuck:
                self._audio_hang = False
                self.clear_notice("audio")
            # Фраза не может обрабатываться вечно: что бы ни сломалось, облачко не «висит».
            loading = self.engine.state in ("loading", "downloading")
            for sess in list(self.jobs.values()):
                ref = sess.ai_since or sess.asr_since
                if sess.text is not None or ref is None:
                    continue
                if now - ref > (LOADING_JOB_MAX_S if loading else JOB_MAX_S):
                    log.error("Фраза #%d обрабатывается %.0f с — освобождаю очередь", sess.sid, now - ref)
                    if not sess.raw:
                        self._keep_unrecognized(sess)       # звук — в историю, его можно повторить
                    elif sess.kind == "edit":
                        self._skip(sess)                    # правка не удалась — выделенное не трогаем
                    else:
                        sess.ai_since = None                # текст есть — вставляем без ИИ
                        self._after_correction(sess, getattr(sess, "pre", "") or sess.raw)
                elif loading and now - ref > 30 and not getattr(sess, "told_loading", False):
                    sess.told_loading = True
                    if self.rec is None:
                        self.bubble.show_message("Распознавание ещё загружается — фраза не потеряется", "info",
                                                 3000)
            if self._restart_reason and self.rec is None and not self.jobs and not self._outbox \
                    and not self._insert_busy and self._tail is None:
                self._restart_now()
        except Exception:  # noqa: BLE001
            log.exception("Сбой сторожа")

    # ---------------------------------------------------------------- обслуживание
    def _maintenance(self) -> None:
        """Раз в час: удалить старую историю (по умолчанию старше недели) и звук, проверить место."""
        keep = int(self.settings.get("general.history_days", 7) or 0)
        audio_days = int(self.settings.get("audio.keep_audio_days", 3) or 3)
        if keep:
            audio_days = min(audio_days, keep)
        self.history.purge(keep, audio_days)
        threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()

    def _housekeeping(self) -> None:
        from .config import CONFIG_DIR, DATA_DIR, STATE_DIR
        try:
            free_mb = shutil.disk_usage(DATA_DIR).free // (1 << 20)
        except OSError:
            free_mb = -1
        self.sig_disk.emit(int(free_mb))
        # Отложенные повреждённые файлы (*.broken-*) — не дольше 30 дней.
        cutoff = time.time() - 30 * 86400
        for folder in (CONFIG_DIR, DATA_DIR):
            try:
                for entry in os.scandir(folder):
                    if ".broken-" in entry.name and entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
            except OSError:
                pass
        # Журнал аварий не растёт бесконечно.
        crash = STATE_DIR / "crash.log"
        try:
            if crash.stat().st_size > 1_000_000:
                with open(crash, "rb") as fh:
                    fh.seek(-200_000, os.SEEK_END)
                    tail = fh.read()
                with open(crash, "wb") as fh:
                    fh.write(tail)
        except OSError:
            pass

    @Slot(int)
    def _on_disk(self, free_mb: int) -> None:
        self._low_disk = 0 <= free_mb < LOW_DISK_MB
        if self._low_disk:
            self.set_notice("disk", "warn", f"На диске почти не осталось места ({free_mb} МБ). Aqua продолжает "
                            "работать, но не сохраняет звук записей. Освободите место — всё вернётся само.", "")
        else:
            self.clear_notice("disk")

    def _on_audio_hang(self, stuck: float) -> None:
        if self._audio_hang:
            return
        self._audio_hang = True
        log.error("Звуковая система не отвечает %.0f с (%s)", stuck, getattr(self.recorder, "_busy_what", ""))
        if self.rec is not None:
            self.finish()
        self.bubble.show_message("Звук не отвечает — Aqua перезапустится сама", "warn", 4000)
        self.set_notice("audio", "error", "Звуковая система (PipeWire) перестала отвечать. Aqua перезапустит "
                        "себя, как только закончит текущие фразы. Если не поможет — перезапустите звук: "
                        "systemctl --user restart pipewire pipewire-pulse wireplumber", "")
        self._request_restart("звуковая система не отвечает")

    def _request_restart(self, reason: str) -> None:
        """Перезапустить процесс (сторож поднимет его заново) — когда не будет записи и обработки."""
        if os.environ.get("AQUA_SUPERVISED") != "1":
            return
        from .config import restart_allowed
        if not restart_allowed():
            log.warning("Перезапуск (%s) пропущен: недавно уже перезапускались", reason)
            return
        self._restart_reason = reason

    def _restart_now(self) -> None:
        from .config import RESTART_CODE, note_restart
        log.warning("Перезапускаюсь: %s", self._restart_reason)
        if self._restart_reason != "по команде":
            note_restart(self._restart_reason)     # самоперезапуски ограничены, просьбы человека — нет
        self._restart_reason = ""
        self.quitting = True
        self.exit_code = RESTART_CODE
        self.qapp.exit(RESTART_CODE)

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
        self._after_resume()

    def _after_resume(self) -> None:
        if time.monotonic() - self._resumed_at < 10:
            return      # уже починили (сигнал logind и сторож могли сработать оба)
        self._resumed_at = time.monotonic()
        self._clock = (time.time(), time.monotonic())
        llm.reset_connections()
        pwaudio.invalidate()
        # Питание могло смениться за время сна: перечитываем в фоне (powerprofilesctl может тормозить).
        threading.Thread(target=self._refresh_power, name="power", daemon=True).start()
        if self.rec is not None:
            self.recorder.reopen(self.settings.get("audio.input_device"))
        elif getattr(self.recorder, "warm", False):
            self.recorder.set_warm(False)
            if self.settings.get("audio.keep_mic_warm") and not perf.economy(self.settings):
                self.recorder.set_warm(True, self.settings.get("audio.input_device"))
        # После сна драйвер NVIDIA иногда теряет контекст CUDA: проверяем распознаватель коротким
        # тестом, при ошибке он сам перезапустится (и при необходимости уйдёт на процессор).
        self.engine.health_check(lambda ok: None if ok else self.sig_heal_engine.emit("сбой после сна"))
        self.clear_notice("clip")

    @Slot(bool)
    @guarded
    def _on_lock(self, locked: bool) -> None:
        if locked:
            if self.rec is not None:
                log.info("Экран заблокирован во время записи — заканчиваю её")
                self.finish()
        else:
            self._sync_keys()
            QTimer.singleShot(700, self._pump_insert)

    @Slot(bool)
    @guarded
    def _on_sleep(self, going: bool) -> None:
        if going:
            # Микрофон и соединения сон всё равно сломает — закрываем аккуратно сами.
            if self.rec is not None:
                self.finish()
            if getattr(self.recorder, "warm", False):
                self.recorder.set_warm(False)
            llm.reset_connections()
        else:
            self._resumed_at = 0.0
            self._after_resume()
            if self.settings.get("audio.keep_mic_warm") and not perf.economy(self.settings) and self.rec is None:
                self.recorder.set_warm(True, self.settings.get("audio.input_device"))

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
            self.insert_text(text, send=send, window=sess.window)
        elif send:
            self.insert_text("", send=True, window=sess.window)
        # Пустые записи (тишина, отменённые) в историю не попадают.
        if not self.settings.get("general.privacy_mode", False) and text.strip():
            audio = info.get("audio")
            if not self.settings.get("audio.save_audio", True) or audio is None or not len(audio) \
                    or self._low_disk:
                audio = None
            mode = {"edit": "edit"}.get(sess.kind, "hands-free" if sess.mode == "hands_free" else "dictation")
            if sess.ai and sess.kind != "edit":
                mode += "+ai"
            # Звук и запись сохраняются в фоне: медленный диск не тормозит облачко.
            self.history.add(text.strip(), raw, sess.window.app, sess.window.title, mode,
                             float(info.get("duration", 0.0)), count_words(text),
                             float(info.get("latency", 0.0)), audio)

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

    def insert_text(self, text: str, send: bool = False, window: Optional[WindowInfo] = None) -> None:
        """Поставить текст в очередь вставки: по одному, по порядку, без гонок за буфер обмена."""
        self._outbox.append((text, send, window))
        self._pump_insert()

    @guarded
    def _pump_insert(self) -> None:
        if self._insert_busy or not self._outbox:
            if not self._outbox:
                self._held_by_lock = False
            return
        if self.sysevents.locked:
            # Экран заблокирован: вставка ушла бы в поле пароля. Ждём разблокировки.
            if not self._held_by_lock:
                log.info("Экран заблокирован — вставка отложена до разблокировки")
            self._held_by_lock = True
            return
        if self.keys.any_modifier():
            # Человек держит Alt (новая диктовка) или Ctrl — Ctrl+V превратился бы в Alt+Ctrl+V.
            # Ждём отпускания. Модификаторы НЕ отпускаем за человека: фальшивое отпускание
            # Alt оборвало бы новую запись.
            now = time.monotonic()
            if self._insert_wait_since is None:
                self._insert_wait_since = now
            if self.rec is None and not self._act_down and now - self._insert_wait_since > 8:
                text, _send, _window = self._outbox.popleft()
                self._insert_wait_since = None
                self._set_clipboard(text, hide_from_history=False)
                self.bubble.show_message("Текст в буфере — вставьте Ctrl+V", "warn", 3000)
                QTimer.singleShot(0, self._pump_insert)
                return
            QTimer.singleShot(25, self._pump_insert)
            return
        self._insert_wait_since = None
        text, send, window = self._outbox.popleft()
        if self._held_by_lock and text and window is not None and window.wm_class:
            # Текст ждал разблокировки экрана. Вставляем, только если перед нами то же окно.
            now_window = self.desktop.active_window()
            if (now_window.wm_class, now_window.title) != (window.wm_class, window.title):
                self._set_clipboard(text.strip(), hide_from_history=False)
                self.bubble.show_message("Текст диктовки в буфере обмена — вставьте Ctrl+V", "info", 4000)
                QTimer.singleShot(0, self._pump_insert)
                return
        self._insert_busy = True
        self._insert_started = time.monotonic()
        # Набор по буквам длинного текста идёт долго — это не зависание.
        self._insert_limit = 8.0 + (len(text) * 0.03 if self.settings.get("insert.method", "paste") == "type" else 0)
        if not text:
            self._press_send()
            QTimer.singleShot(60, self._insert_done)
            return
        method = self.settings.get("insert.method", "paste")
        if method == "type":
            def work():
                ok = xdotool_type(text)
                self.sig_typed.emit(ok, text, send)
            threading.Thread(target=work, daemon=True).start()
            return
        if method == "clipboard":
            self._set_clipboard(text, hide_from_history=False)
            self.bubble.show_message("Скопировано в буфер обмена", "info", 1500)
            QTimer.singleShot(0, self._insert_done)
            return
        self._paste(text, send)

    @Slot(bool, str, bool)
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
        elif self._clip_snapped or self._clip_text:
            self._restore_clipboard()

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
        pre = self._prefetched
        if restore and not self._clip_snapped and pre is not None and pre[1] == self._clip_gen \
                and time.monotonic() - pre[2] < 30:
            # Снимок сделан заранее (пока распознавалась фраза), и буфер с тех пор не менялся.
            self._prefetched = None
            self._clip_snapped = True
            self._clip_restore = pre[0]
        if restore and not self._clip_snapped:
            # Снимок буфера — один раз на серию вставок (вернём его после последней) и в фоне:
            # зависшая программа-владелец буфера не должна подвешивать облачко.
            self._snap_seq += 1
            seq = self._snap_seq
            self._snap_pending = (seq, text, send)

            def work():
                self.sig_snapshot.emit(xclip.snapshot(timeout=0.6), seq)
            threading.Thread(target=work, name="clipboard", daemon=True).start()
            QTimer.singleShot(900, lambda: self._on_snapshot(None, seq))
            return
        self._paste_now(text, send)

    @Slot(object, int)
    @guarded
    def _on_snapshot(self, snap, seq: int) -> None:
        pending = self._snap_pending
        if pending is None or pending[0] != seq:
            return          # уже вставили (снимок опоздал — буфер не вернём, но и не ждём)
        self._snap_pending = None
        self._clip_snapped = True
        self._clip_restore = snap
        self._paste_now(pending[1], pending[2])

    def _prefetch_clipboard(self) -> None:
        """Снять буфер заранее (в момент отпускания клавиши): к вставке снимок уже готов."""
        if not self.settings.get("insert.restore_clipboard", True) or self._clip_snapped or self._prefetching \
                or self.settings.get("insert.method", "paste") != "paste":
            return
        self._prefetching = True
        gen = self._clip_gen

        def work():
            self.sig_prefetch.emit(xclip.snapshot(timeout=0.6), gen)
        threading.Thread(target=work, name="clipboard", daemon=True).start()

    @Slot(object, int)
    def _on_prefetch(self, snap, gen: int) -> None:
        self._prefetching = False
        self._prefetched = (snap, gen, time.monotonic()) if snap is not None and gen == self._clip_gen else None

    def _clip_changed(self) -> None:
        self._clip_gen += 1

    def _paste_now(self, text: str, send: bool) -> None:
        restore = self.settings.get("insert.restore_clipboard", True)
        self._clip_text = text if restore else ""
        self._set_clipboard(text, hide_from_history=restore)

        def keys():
            window = self.desktop.active_window()
            if not window.wm_class and not window.title:
                # Некуда вставлять (окно закрылось, фокус на рабочем столе): текст оставляем
                # в буфере обмена, старый буфер не возвращаем — иначе текст пропал бы.
                self._clip_restore, self._clip_text, self._clip_snapped = None, "", False
                self.bubble.show_message("Некуда вставить — текст в буфере обмена (Ctrl+V)", "info", 3500)
                return
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
        self._clip_snapped = False
        ours, self._clip_text = self._clip_text, ""
        if not ours or snap is None:
            return          # снимка нет (владелец буфера не ответил) — оставляем наш текст
        clip = QGuiApplication.clipboard()
        if not clip.ownsClipboard():
            return          # человек уже скопировал что-то своё (без запроса к чужой программе)
        if snap:
            md = QMimeData()
            for mime, data in snap.items():
                md.setData(mime, QByteArray(data))
            clip.setMimeData(md, QClipboard.Clipboard)
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
        if key == "mic" or (key == "mute" and self.notices.get("mute", ("", "", ""))[2] == "Выбрать микрофон"):
            self.clear_notice(key)
            self.show_window("settings")
            return
        if key == "mute":
            device = self.settings.get("audio.input_device")

            def unmute():
                ok = pwaudio.unmute(device)
                self.sig_notice_done.emit("mute", ok, "Микрофон включён." if ok else
                                          "Не получилось включить микрофон — включите его в настройках звука.")
            threading.Thread(target=unmute, daemon=True).start()
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
        elif key == "mute":
            self.set_notice("mute", "info" if ok else "error", message, "")
            if ok:
                QTimer.singleShot(8000, lambda: self.clear_notice("mute"))

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
        elif key in ("general.history_days", "audio.keep_audio_days"):
            # Срок хранения сократили — удаляем не сразу: вдруг выбрали случайно и вернут обратно.
            if not hasattr(self, "_purge_later"):
                self._purge_later = QTimer(self)
                self._purge_later.setSingleShot(True)
                self._purge_later.timeout.connect(self._maintenance)
            self._purge_later.start(60_000)
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
            self._mic_test = True
            self._mic_test_since = time.monotonic()

            def result(ok, _fallback, error):
                if not ok:
                    self.sig_mic_result.emit(MIC_TEST_SID, False, False, friendly_mic_error(error))

            self.recorder.start(self.settings.get("audio.input_device"), self._mic_sink, preroll_blocks=0,
                                on_result=result)
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
        try:
            audio = load_wav(row["audio"])
        except Exception as exc:  # noqa: BLE001 — файл записи повреждён
            log.warning("Не удалось прочитать запись %s: %s", row["audio"], exc)
            callback(None)
            return
        self._retranscribe_callbacks[row_id] = callback
        self.engine.transcribe_array(audio, lambda text: self.sig_retranscribed.emit(row_id, text or ""))

    @Slot(int, str)
    def _on_retranscribed(self, row_id: int, text: str) -> None:
        callback = self._retranscribe_callbacks.pop(row_id, None)
        processed = self.processor.process(text, self.settings) if text else ""
        if processed:
            self.history.update_text(row_id, processed, count_words(processed))
        if callback:
            callback(processed)


def one_line(text: str) -> str:
    """Текст одной строкой (для терминала). Строчная буква после бывшего переноса, если там
    не кончалось предложение: «первая команда\nВторая команда» → «первая команда вторая команда»."""
    def join(m):
        before = text[:m.start()].rstrip()
        nxt = m.group(1)
        if before and before[-1] not in ".!?…:":
            nxt = nxt.lower()
        return " " + nxt
    return re.sub(r"\s*\n+\s*(\S)", join, text).strip()


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
