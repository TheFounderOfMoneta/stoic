"""Контроллер приложения: клавиши → запись → распознавание → обработка → вставка."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from . import llm
from .asr.engine import ASREngine
from .audio import Recorder, SoundPlayer, audio_backend_error
from .config import APP_ID, Settings
from .hotkeys import (MODIFIER_GENERIC, MODIFIER_TOKENS, HotkeyRecorder, KeyState, SelectionTracker,
                      X11Grabber, X11KeyListener, pretty_combo, read_x_selection)
from .inserter import X11Desktop, WindowInfo, xdotool_type
from .postprocess import TextProcessor, casual, count_words, is_messenger, split_send_it
from .storage import DEFAULT_DICTIONARY, DEFAULT_REPLACEMENTS, History, JsonStore, save_wav
from .sysaudio import SystemAudio

log = logging.getLogger(__name__)

IPC_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"{APP_ID}-{os.getuid()}.sock")
SAFE_MIME = ("text/", "image/png", "x-special/", "application/x-kde", "chromium/")


class Session:
    def __init__(self, sid: int, mode: str, kind: str, window: WindowInfo, selection: str = ""):
        self.sid = sid
        self.mode = mode            # hold | hands_free
        self.kind = kind            # dictation | edit
        self.window = window
        self.selection = selection
        self.started = time.monotonic()
        self.t_down = time.monotonic()


class App(QObject):
    # Мосты из фоновых потоков в главный поток Qt
    sig_key = Signal(str, str, float)
    sig_level = Signal(float)
    sig_status = Signal(str, str)
    sig_partial = Signal(int, str)
    sig_final = Signal(int, str, object)
    sig_llm = Signal(int, str, str)
    sig_retranscribed = Signal(int, str)
    # Для окна настроек
    status_changed = Signal(str, str)
    history_changed = Signal()
    hotkey_captured = Signal(list, bool)    # (сочетание, окончательно)
    mic_level = Signal(float)
    activate_seen = Signal()

    def __init__(self, qapp: QApplication, settings: Settings):
        super().__init__()
        self.qapp = qapp
        self.settings = settings
        self.history = History()
        self.dictionary = JsonStore("dictionary.json", DEFAULT_DICTIONARY)
        self.replacements = JsonStore("replacements.json", DEFAULT_REPLACEMENTS)
        self.processor = TextProcessor()
        self.reload_dictionary()

        self.sounds = SoundPlayer(settings.get("audio.sound_volume", 0.35))
        self.sysaudio = SystemAudio()
        self.desktop = X11Desktop()
        self.keys = KeyState()
        self.hotkey_recorder = HotkeyRecorder()
        self.grabber: Optional[X11Grabber] = None
        self.listener: Optional[X11KeyListener] = None
        self.selection = SelectionTracker()

        self.engine = ASREngine(settings, self.sig_status.emit, self.sig_partial.emit, self.sig_final.emit)
        self.recorder = Recorder(self._on_audio, self.sig_level.emit)
        self.engine_state = ("loading", "Загрузка…")

        self.phase = "idle"              # idle | recording | processing
        self.session: Optional[Session] = None
        self._sid = 0
        self._act_down = False
        self._ignore_release = False
        self._tap_pending = False
        self._ignore_names: dict[str, int] = {}
        self._last_text = ""
        self._last_insert = None
        self._mic_test = False
        self._clip_restore: Optional[QMimeData] = None
        self._clip_text = ""

        from .ui.bubble import Bubble
        self.bubble = Bubble(settings)
        self.bubble.clicked.connect(lambda: self.start("hands_free"))
        self.bubble.stop_clicked.connect(self.finish)
        self.bubble.menu_requested.connect(self._bubble_menu)

        self.sig_key.connect(self._on_key)
        self.sig_level.connect(self._on_level)
        self.sig_status.connect(self._on_status)
        self.sig_partial.connect(self._on_partial)
        self.sig_final.connect(self._on_final)
        self.sig_llm.connect(self._on_llm)
        self.sig_retranscribed.connect(self._on_retranscribed)
        self._retranscribe_callbacks: dict[int, object] = {}
        self.settings.on_change(self._on_setting)

        self.window = None
        self.tray = None
        self.quitting = False
        self.server: Optional[QLocalServer] = None

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
        self.bubble.set_model_loading(True)
        self.bubble.apply_targets()
        if self.settings.get("bubble.show", True):
            self.bubble.reposition()
            self.bubble.show()
            self.bubble._kick()
        self.engine.start()
        if self.settings.get("audio.keep_mic_warm"):
            self.recorder.set_warm(True, self.settings.get("audio.input_device"))
        self._start_ipc()
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
        for step in (lambda: self.cancel(silent=True) if self.phase != "idle" else None,
                     lambda: self.listener and self.listener.stop(),
                     lambda: self.grabber and self.grabber.ungrab_all(),
                     self.engine.shutdown,
                     lambda: self.recorder.set_warm(False),
                     self.sysaudio.end):
            try:
                step()
            except Exception:  # noqa: BLE001
                log.debug("Ошибка при завершении", exc_info=True)

    def hint_text(self) -> str:
        combos = self.settings.get("hotkeys.activate") or []
        if not combos:
            return "Щёлкните, чтобы диктовать"
        return f"Удерживайте {pretty_combo(combos[0])}"

    def reload_dictionary(self) -> None:
        self.processor.load(self.dictionary.data.get("terms", []),
                            self.replacements.data.get("replacements", []))

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
            if self.phase == "recording":
                self.finish()
            elif self.phase == "idle":
                self.start("hands_free")
        elif cmd == "start":
            if self.phase == "idle":
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
        if not self.settings.get("hotkeys.enabled", True):
            return
        for key in ("paste_last", "hands_free"):
            for combo in self._combos(key):
                if any(t not in MODIFIER_GENERIC and t.lstrip("lr") not in MODIFIER_GENERIC for t in combo):
                    self.grabber.grab(combo)

    def _grab_cancel(self, on: bool) -> None:
        if not self.grabber:
            return
        for combo in self._combos("cancel"):
            if on:
                self.grabber.grab(combo, any_modifier=len(combo) == 1)
            else:
                self.grabber.ungrab(combo, any_modifier=len(combo) == 1)

    @Slot(str, str, float)
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
            if self.phase in ("recording", "processing") and any(self.keys.matches(c) for c in self._combos("cancel")):
                self.cancel()
                return
            if any(self.keys.matches(c) for c in self._combos("paste_last")):
                if self.phase == "recording" and self.session and self.session.mode == "hold":
                    self.cancel(silent=True)
                self.paste_last()
                return
            if any(self.keys.matches(c) for c in self._combos("hands_free")):
                if self.phase == "idle":
                    self.start("hands_free")
                elif self.phase == "recording":
                    self.finish()
                return
            if any(self.keys.matches(c) for c in activate):
                if not self._act_down:
                    self._act_down = True
                    self.activate_seen.emit()
                    self._activate_press(t)
                    if self.phase != "idle":
                        self._neutralize_modifier()
                return
            # Нажата посторонняя клавиша во время удержания — это сочетание (Alt+Tab и т.п.).
            if self.phase == "recording" and self.session and self.session.mode == "hold" \
                    and name not in MODIFIER_TOKENS:
                self.cancel(silent=True)
        else:
            if self._act_down and not any(self.keys.contains(c) for c in activate):
                self._act_down = False
                self._activate_release(t)

    def _activate_press(self, t: float) -> None:
        if self.phase == "idle":
            self.start("hold")
        elif self.phase == "recording" and self.session:
            if self._tap_pending:
                # Второе нажатие подряд — режим «без рук», запись продолжается без разрыва.
                self._tap_pending = False
                self._ignore_release = True
                self._set_mode("hands_free")
            elif self.session.mode == "hands_free":
                self._ignore_release = True
                self.finish()

    def _activate_release(self, t: float) -> None:
        if self._ignore_release:
            self._ignore_release = False
            return
        sess = self.session
        if self.phase != "recording" or sess is None or sess.mode != "hold":
            return
        held_ms = (t - sess.t_down) * 1000
        if held_ms < float(self.settings.get("hotkeys.tap_threshold_ms", 280)):
            if self.settings.get("hotkeys.double_tap_hands_free", True):
                self._tap_pending = True
                sid = sess.sid
                QTimer.singleShot(int(self.settings.get("hotkeys.double_tap_window_ms", 380)),
                                  lambda: self._tap_timeout(sid))
            else:
                self.cancel(silent=True)
        else:
            self.finish()

    def _tap_timeout(self, sid: int) -> None:
        if self._tap_pending and self.session and self.session.sid == sid and self.phase == "recording":
            self._tap_pending = False
            self.cancel(silent=True)

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

    # ================================================================ запись
    def start(self, mode: str) -> None:
        if self.phase != "idle":
            return
        window = self.desktop.active_window()
        kind, selection = "dictation", ""
        if self.settings.get("edit_mode.enabled", True) and self.selection.is_live():
            text = read_x_selection("PRIMARY")
            if text.strip() and len(text) <= int(self.settings.get("edit_mode.max_chars", 6000)):
                kind, selection = "edit", text
        self._sid += 1
        sess = Session(self._sid, mode, kind, window, selection)
        streaming = self.settings.get("asr.streaming", "hands_free")
        preview = streaming == "always" or (streaming == "hands_free" and mode == "hands_free")
        self.engine.begin(sess.sid, preview)
        try:
            self.recorder.start(self.settings.get("audio.input_device"))
        except Exception as exc:  # noqa: BLE001
            log.exception("Микрофон недоступен")
            self.engine.cancel(sess.sid)
            self.bubble.show_message(f"Микрофон недоступен: {exc}"[:80], "error", 4000)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("error")
            return
        self.session = sess
        self.phase = "recording"
        self._tap_pending = False
        self._ignore_release = mode == "hands_free" and self._act_down
        self.bubble.set_live_text("")
        self.bubble.set_chip(f"{count_words(selection)} {plural_words(count_words(selection))} выделено"
                             if kind == "edit" else "")
        self.bubble.set_state("listening" if mode == "hold" else "hands_free")
        if self.tray:
            self.tray.set_recording(True)
        self._grab_cancel(True)
        if self.settings.get("audio.sounds", True):
            self.sounds.volume = float(self.settings.get("audio.sound_volume", 0.35))
            self.sounds.play("start")
        sid = sess.sid
        # Приглушаем фон с задержкой: случайное короткое нажатие не дёргает громкость.
        QTimer.singleShot(300, lambda: self._begin_background(sid))
        limit_ms = int(float(self.settings.get("audio.max_minutes", 20)) * 60_000)
        QTimer.singleShot(limit_ms, lambda: self._limit_reached(sid))

    def _begin_background(self, sid: int) -> None:
        if self.phase == "recording" and self.session and self.session.sid == sid and not self._tap_pending:
            self.sysaudio.begin(self.settings.get("audio.while_dictating", "mute"))

    def _limit_reached(self, sid: int) -> None:
        if self.phase == "recording" and self.session and self.session.sid == sid:
            self.finish()

    def _set_mode(self, mode: str) -> None:
        if not self.session:
            return
        self.session.mode = mode
        if mode == "hands_free":
            self.bubble.set_state("hands_free")
            if self.settings.get("asr.streaming", "hands_free") in ("hands_free", "always"):
                self.engine.set_preview(self.session.sid, True)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("lock")
            self.sysaudio.begin(self.settings.get("audio.while_dictating", "mute"))

    def _on_audio(self, block) -> None:
        sess = self.session
        if sess is not None:
            self.engine.feed(sess.sid, block)

    @Slot(float)
    def _on_level(self, level: float) -> None:
        self.bubble.set_level(level)
        self.mic_level.emit(level)

    def finish(self) -> None:
        if self.phase != "recording" or self.session is None:
            return
        sess = self.session
        self.phase = "processing"
        self._tap_pending = False
        self.bubble.set_state("processing")
        if self.tray:
            self.tray.set_recording(False)
        tail = int(self.settings.get("audio.tail_ms", 140))

        def stop():
            self.recorder.stop()
            self.engine.finish(sess.sid)
            self.sysaudio.end()
            if self.settings.get("audio.sounds", True):
                QTimer.singleShot(60, lambda: self.sounds.play("stop"))

        QTimer.singleShot(tail, stop)

    def cancel(self, silent: bool = False) -> None:
        if self.phase == "idle":
            return
        sess = self.session
        if sess is not None:
            self.engine.cancel(sess.sid)
        self.recorder.stop()
        self.sysaudio.end()
        self._grab_cancel(False)
        self.phase = "idle"
        self.session = None
        self._tap_pending = False
        if self.tray:
            self.tray.set_recording(False)
        self.bubble.set_live_text("")
        self.bubble.set_chip("")
        if silent:
            self.bubble.set_state("idle")
        else:
            self.bubble.show_message("Отменено", "info", 1200)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("cancel")

    # ================================================================ результаты
    @Slot(str, str)
    def _on_status(self, state: str, message: str) -> None:
        self.engine_state = (state, message)
        self.bubble.set_model_loading(state in ("loading", "downloading"))
        if state == "error":
            self.bubble.show_message("Модель не загрузилась — см. настройки", "error", 5000)
        self.status_changed.emit(state, message)
        if self.tray:
            self.tray.set_status(state, message)

    @Slot(int, str)
    def _on_partial(self, sid: int, text: str) -> None:
        if self.session and self.session.sid == sid and self.phase in ("recording", "processing"):
            self.bubble.set_live_text(text)

    @Slot(int, str, object)
    def _on_final(self, sid: int, text: str, info: dict) -> None:
        sess = self.session
        if sess is None or sess.sid != sid or self.phase != "processing":
            return
        self._grab_cancel(False)
        info = info or {}
        if info.get("error"):
            self._end_session()
            self.bubble.show_message("Ошибка распознавания", "error", 3500)
            if self.settings.get("audio.sounds", True):
                self.sounds.play("error")
            return
        if not text.strip():
            self._end_session()
            self.bubble.show_message("Речь не распознана", "warn", 1800)
            return
        processed = self.processor.process(text, self.settings)
        send = False
        if sess.mode == "hands_free" and self.settings.get("insert.send_it", True):
            processed, send = split_send_it(processed)
        if self.settings.get("text.casual_messaging", False) and sess.kind != "edit" \
                and is_messenger(sess.window.wm_class, sess.window.title):
            processed = casual(processed)
        sess.info = info
        sess.raw = text
        sess.send = send
        use_llm = self.settings.get("llm.enabled", False)
        if sess.kind == "edit" and use_llm:
            self._run_llm(sess, "command", processed)
        elif use_llm and self.settings.get("llm.use_for_dictation", False) and processed:
            self._run_llm(sess, "polish", processed)
        else:
            self._deliver(sess, processed)

    def _run_llm(self, sess: Session, kind: str, text: str) -> None:
        self.bubble.set_live_text("✨ " + ("Правлю выделенный текст…" if kind == "command" else "Оформляю текст…"))
        settings = self.settings
        vocab = self.processor.vocabulary()

        def work():
            try:
                if kind == "command":
                    result = llm.run_command(settings, text, sess.selection, sess.window.app, sess.window.title)
                else:
                    result = llm.polish_dictation(settings, text, sess.window.app, sess.window.title, vocab)
                self.sig_llm.emit(sess.sid, result, "")
            except Exception as exc:  # noqa: BLE001
                log.warning("ИИ-обработка не удалась: %s", exc)
                self.sig_llm.emit(sess.sid, text, str(exc))

        threading.Thread(target=work, daemon=True).start()

    @Slot(int, str, str)
    def _on_llm(self, sid: int, text: str, error: str) -> None:
        sess = self.session
        if sess is None or sess.sid != sid or self.phase != "processing":
            return
        self.bubble.set_live_text("")
        if error:
            self.bubble.show_message("ИИ недоступен — вставлен исходный текст", "warn", 2500)
        self._deliver(sess, text, ai=not error)

    def _end_session(self) -> None:
        self.phase = "idle"
        self.session = None
        self.bubble.set_live_text("")
        self.bubble.set_chip("")
        if self.bubble.state != "message":
            self.bubble.set_state("idle")

    def _deliver(self, sess: Session, text: str, ai: bool = False) -> None:
        info = getattr(sess, "info", {}) or {}
        raw = getattr(sess, "raw", text)
        send = getattr(sess, "send", False)
        if text and self.settings.get("insert.trailing_space", True) and not send and sess.kind != "edit" \
                and not text.endswith((" ", "\n")):
            text += " "
        self._end_session()
        last = self._last_insert
        if text and sess.kind != "edit" and last and last[0] == (sess.window.wm_class, sess.window.title) \
                and time.monotonic() - last[2] < 120 and last[1] and not last[1][-1].isspace() \
                and text[0] not in ",.!?…:;)":
            text = " " + text   # предыдущая вставка в это окно закончилась без пробела
        if text:
            self._last_text = text.lstrip(" ")
            self._last_insert = ((sess.window.wm_class, sess.window.title), text, time.monotonic())
            self.insert_text(text, send=send)
        elif send:
            self._press_send()
        if not self.settings.get("general.privacy_mode", False) and (text or raw):
            audio_path = None
            audio = info.get("audio")
            if self.settings.get("audio.save_audio", True) and audio is not None and len(audio):
                try:
                    audio_path = save_wav(audio)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось сохранить аудио")
            mode = {"edit": "edit"}.get(sess.kind, "hands-free" if sess.mode == "hands_free" else "dictation")
            if ai and sess.kind != "edit":
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

    def insert_text(self, text: str, send: bool = False, attempt: int = 0) -> None:
        # Ждём, пока человек отпустит модификаторы (иначе Ctrl+V превратится в Alt+Ctrl+V).
        if self.keys.any_modifier() and attempt < 40:
            QTimer.singleShot(15, lambda: self.insert_text(text, send, attempt + 1))
            return
        if self.keys.any_modifier():
            held = [name for name in self.keys.down if name in MODIFIER_TOKENS]
            self.desktop.release_modifiers(held)
        method = self.settings.get("insert.method", "paste")
        if method == "type":
            def work():
                if not xdotool_type(text):
                    QTimer.singleShot(0, lambda: self._paste(text, send))
                elif send:
                    self._press_send()
            threading.Thread(target=work, daemon=True).start()
            return
        if method == "clipboard":
            self._set_clipboard(text, hide_from_history=False)
            self.bubble.show_message("Скопировано в буфер обмена", "info", 1500)
            return
        self._paste(text, send)

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

    def _paste(self, text: str, send: bool) -> None:
        restore = self.settings.get("insert.restore_clipboard", True)
        if restore and self._clip_restore is None:
            self._clip_restore = self._snapshot_clipboard()
        self._clip_text = text
        self._set_clipboard(text, hide_from_history=restore)

        def keys():
            window = self.desktop.active_window()
            mods, key = self.desktop.paste_keys_for(window, self.settings.get("insert.terminal_shift_paste", True))
            if not self.desktop.send_combo(mods, key):
                self.bubble.show_message("Не удалось вставить — текст в буфере", "warn", 2500)
            if send:
                QTimer.singleShot(140, self._press_send)

        QTimer.singleShot(30, keys)
        if restore:
            QTimer.singleShot(int(self.settings.get("insert.restore_delay_ms", 450)) + (160 if send else 0),
                              self._restore_clipboard)

    def _restore_clipboard(self) -> None:
        snap, self._clip_restore = self._clip_restore, None
        clip = QGuiApplication.clipboard()
        if clip.text(QClipboard.Clipboard) != self._clip_text:
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
            self.recorder.set_warm(False)
            if self.settings.get("audio.keep_mic_warm"):
                self.recorder.set_warm(True, self.settings.get("audio.input_device"))
        elif key in ("asr.device", "asr.precision", "asr.model_dir", "asr.cpu_threads"):
            self.engine.reload()

    def begin_hotkey_capture(self) -> None:
        self.hotkey_recorder.begin(lambda combo: self.hotkey_captured.emit(list(combo), False),
                                   lambda combo: self.hotkey_captured.emit(list(combo), True))

    def end_hotkey_capture(self) -> None:
        self.hotkey_recorder.active = False

    def mic_test(self, on: bool) -> None:
        """Проверка микрофона на главной странице (без распознавания)."""
        if self.phase != "idle":
            return
        if on and not self._mic_test:
            try:
                self.recorder.start(self.settings.get("audio.input_device"))
                self._mic_test = True
            except Exception as exc:  # noqa: BLE001
                self.status_changed.emit("mic-error", str(exc))
        elif not on and self._mic_test:
            self._mic_test = False
            self.recorder.stop()

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
