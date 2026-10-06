"""Точка входа: aqua-linux [--background] | toggle | start | stop | cancel | paste-last | settings | restart | quit."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections import deque

from .config import (APP_ID, APP_NAME, APP_VERSION, LOG_FILE, PROJECT_ROOT, RESTART_CODE, STATE_DIR, Settings,
                     ensure_dirs)

COMMANDS = ("toggle", "start", "stop", "cancel", "paste-last", "show", "settings", "history", "restart", "quit")
HANG_S = float(os.environ.get("AQUA_HANG_S") or 25)   # главный поток молчит столько — зависло
FIRST_BEAT_S = 240     # запуск (загрузка библиотек на медленном диске) — с большим запасом
STALL_DUMP_S = 12      # интерфейс занят столько — стеки всех потоков в crash.log (для разбора)
_CRASH_FILE = None


def setup_logging(verbose: bool) -> None:
    ensure_dirs()
    handlers: list = []
    try:
        handlers.append(logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2,
                                                             encoding="utf-8"))
    except OSError:
        pass   # папка журнала недоступна — пишем только в консоль, приложение работает
    if sys.stderr and sys.stderr.isatty() or verbose or not handlers:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _display_alive() -> bool:
    if not os.environ.get("DISPLAY"):
        return False
    try:
        from Xlib import display
        display.Display().close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _instance_lock():
    """Замок «уже запущено»: второй экземпляр (автозапуск + ярлык, двойной щелчок) не стартует,
    даже если первый сейчас не отвечает. Замок снимается ядром при любом завершении процесса."""
    import fcntl
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fh = open(STATE_DIR / "instance.lock", "a+")   # noqa: SIM115 — держим открытым до выхода
    except OSError:
        return True          # не получилось создать файл — работаем без замка
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _watch_child(proc, beat_fd: int, log) -> int:
    """Ждать завершения приложения, следя за его пульсом. Нет пульса HANG_S — зависло:
    снимаем стеки (в crash.log) и завершаем, сторож запустит заново."""
    last_beat = None
    silent = 0.0
    quitting_since = None
    restarting = False
    prev = time.monotonic()
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        try:
            ready, _, _ = select.select([beat_fd], [], [], 1.0)
        except InterruptedError:
            continue
        now = time.monotonic()
        step, prev = now - prev, now
        if ready:
            try:
                data = os.read(beat_fd, 4096)
            except BlockingIOError:
                data = b"."
            except OSError:
                data = b""
            if not data:                     # приложение закрыло канал — оно завершается
                try:
                    return proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.error("%s не завершается — завершаю принудительно", APP_NAME)
                    proc.kill()
                    proc.wait()
                    if restarting:
                        return RESTART_CODE
                    return 0 if quitting_since else -9
            if b"Q" in data or b"R" in data:
                quitting_since = now
                restarting = b"R" in data
            last_beat, silent = now, 0.0
            continue
        if quitting_since is not None and now - quitting_since > 10:
            log.error("%s не завершилась за 10 с после выхода — завершаю принудительно", APP_NAME)
            proc.kill()
            proc.wait()
            return RESTART_CODE if restarting else 0
        if step > 5:
            continue          # компьютер спал или сторожа не планировали — это не зависание
        silent += step
        limit = HANG_S if last_beat is not None else FIRST_BEAT_S
        if silent > limit:
            log.error("%s не отвечает %.0f с — перезапускаю (стеки потоков — в crash.log)", APP_NAME, silent)
            try:
                proc.send_signal(signal.SIGUSR1)       # faulthandler: снимок стеков
                time.sleep(1.0)
            except OSError:
                pass
            proc.kill()
            proc.wait()
            return -9


def supervise(child_args: list[str], verbose: bool) -> int:
    """«Сторож»: запускает приложение отдельным процессом и сам перезапускает его после сбоя
    или зависания. Нормальный выход (меню «Выйти», завершение сеанса) — код 0, тогда сторож
    тоже выходит."""
    setup_logging(verbose)
    log = logging.getLogger(APP_ID + ".supervisor")
    crashes: deque = deque(maxlen=5)
    state = {"proc": None, "stopping": False}

    def on_signal(signum, _frame):
        state["stopping"] = True
        proc = state["proc"]
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    args = list(child_args)
    while True:
        beat_r, beat_w = os.pipe()
        env = dict(os.environ, AQUA_SUPERVISED="1", AQUA_HEARTBEAT_FD=str(beat_w), PYTHONUTF8="1")
        try:
            proc = subprocess.Popen([sys.executable, "-m", "aqualinux", "--child", *args], env=env,
                                    pass_fds=(beat_w,))
        finally:
            os.close(beat_w)
        state["proc"] = proc
        try:
            rc = _watch_child(proc, beat_r, log)
        finally:
            os.close(beat_r)
        if state["stopping"] or rc == 0:
            return 0
        args = ["--background"]           # после сбоя окно заново не открываем
        if rc == RESTART_CODE:
            continue
        if not _display_alive():
            log.info("Графический сеанс закрыт — выхожу")
            return 0
        now = time.monotonic()
        crashes.append(now)
        delay = 1.0
        if len(crashes) == crashes.maxlen and now - crashes[0] < 120:
            delay = 30.0                  # падает раз за разом — даём системе передохнуть
        log.error("%s завершилась с ошибкой (код %s) — перезапуск через %.0f с", APP_NAME, rc, delay)
        time.sleep(delay)


class _Heartbeat:
    """Пульс главного потока: раз в секунду пишем сторожу «жив». Заметные подвисания
    интерфейса — в журнал, долгие — со стеками всех потоков (crash.log)."""

    def __init__(self, log):
        self.log = log
        self.fd = None
        value = os.environ.get("AQUA_HEARTBEAT_FD", "")
        if value.isdigit():
            try:
                self.fd = int(value)
                os.set_blocking(self.fd, False)
            except OSError:
                self.fd = None
        self.last = time.monotonic()
        self.exit_code = lambda: 0

    def tick(self) -> None:
        now = time.monotonic()
        gap, self.last = now - self.last, now
        if gap > 3.0:
            self.log.warning("Интерфейс не отвечал %.1f с", gap)
        self._write(b".")
        if _CRASH_FILE is not None:
            try:
                import faulthandler
                faulthandler.dump_traceback_later(STALL_DUMP_S, repeat=False, file=_CRASH_FILE, exit=False)
            except Exception:  # noqa: BLE001
                pass

    def quitting(self) -> None:
        """Приложение выходит (или перезапускается): сторож не путает долгий выход с зависанием."""
        self._write(b"R" if self.exit_code() == RESTART_CODE else b"Q")
        # Выход не должен зависнуть (поток, застрявший в драйвере или звуковой системе):
        # через 8 с завершаемся принудительно — даже если завис сам выход.
        code = self.exit_code()
        killer = threading.Timer(8.0, lambda: os._exit(code))
        killer.daemon = True
        killer.start()
        try:
            import faulthandler
            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001
            pass

    def _write(self, data: bytes) -> None:
        if self.fd is None:
            return
        try:
            os.write(self.fd, data)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self.fd = None       # сторож закрыл канал


def _install_crash_guards(log) -> None:
    """Необработанная ошибка где угодно — только запись в журнал, приложение продолжает работать."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc, tb)
        log.error("Необработанная ошибка (приложение продолжает работу)", exc_info=(exc_type, exc, tb))

    def thread_hook(args):
        name = args.thread.name if args.thread else "?"
        log.error("Ошибка в потоке %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook
    global _CRASH_FILE
    try:
        import faulthandler
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _CRASH_FILE = open(STATE_DIR / "crash.log", "a", encoding="utf-8")   # noqa: SIM115
        faulthandler.enable(_CRASH_FILE)
        faulthandler.register(signal.SIGUSR1, file=_CRASH_FILE, all_threads=True)   # сторож просит стеки
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} — локальная голосовая диктовка")
    parser.add_argument("command", nargs="?", choices=COMMANDS, help="команда уже запущенному приложению")
    parser.add_argument("--background", action="store_true", help="запуск без окна (автозапуск)")
    parser.add_argument("--no-supervisor", action="store_true", help="без автоматического перезапуска")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")   # нужен X11: XRecord/XTest/override-redirect
    os.environ["RESOURCE_NAME"] = APP_ID               # WM_CLASS=aqua-linux: значок в доке, Blur My Shell
    from .app import send_ipc

    # Уже запущен? Тогда просто передаём команду (или показываем окно).
    if send_ipc(args.command or ("noop" if args.background else "show")):
        return 0
    if args.command in ("stop", "cancel", "paste-last", "quit", "restart"):
        print(f"{APP_NAME} не запущен", file=sys.stderr)
        return 1
    if not args.child:
        lock = _instance_lock()
        if lock is None:
            # Уже запущено, но пока не ответило (стартует или зависло — его сторож разберётся).
            for _ in range(16):
                time.sleep(0.5)
                if send_ipc(args.command or ("noop" if args.background else "show")):
                    return 0
            print(f"{APP_NAME} уже запущена, но пока не отвечает — подождите немного", file=sys.stderr)
            return 0
        globals()["_INSTANCE_LOCK"] = lock
    if not args.child and not args.no_supervisor and os.environ.get("AQUA_NO_SUPERVISOR") != "1":
        return supervise([a for a in raw_args if a != "--no-supervisor"], args.verbose)

    from PySide6.QtCore import Qt
    setup_logging(args.verbose)
    log = logging.getLogger(APP_ID)
    _install_crash_guards(log)
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        log.warning("Сеанс Wayland: глобальные клавиши через XWayland работать не будут. "
                    "Назначьте в GNOME сочетание на команду «aqua-linux toggle».")

    from PySide6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setApplicationName(APP_ID)
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setDesktopFileName(APP_ID)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setStyle("Fusion")

    from .ui.theme import app_icon, apply_app_font
    qapp.setWindowIcon(app_icon())
    apply_app_font(qapp)

    settings = Settings()
    from .app import App
    from .ui.tray import Tray
    app = App(qapp, settings)
    app.start_services()
    if QSystemTrayAvailable():
        app.tray = Tray(app)
        app.tray.show()

    if not settings.get("onboarding_done"):
        from .autostart import set_autostart
        set_autostart(bool(settings.get("general.autostart", True)))
        settings.set("onboarding_done", True)
        app.show_window("home")
    elif args.command in ("show", "settings", "history"):
        app.show_window({"show": "home"}.get(args.command, args.command))
    elif not args.background:
        app.show_window("home")
    if args.command in ("toggle", "start"):
        app.command(args.command)

    signal.signal(signal.SIGINT, lambda *_: app.quit_app())
    signal.signal(signal.SIGTERM, lambda *_: app.quit_app())
    # Таймер, чтобы Python успевал обрабатывать сигналы внутри цикла Qt.
    from PySide6.QtCore import QTimer
    # Он же — пульс для сторожа: главный поток жив.
    heartbeat = _Heartbeat(log)
    heartbeat.exit_code = lambda: app.exit_code
    tick = QTimer()
    tick.timeout.connect(heartbeat.tick)
    tick.start(1000)
    heartbeat.tick()
    qapp.aboutToQuit.connect(heartbeat.quitting)
    qapp.aboutToQuit.connect(app.shutdown)
    log.info("%s %s запущен (%s)", APP_NAME, APP_VERSION, PROJECT_ROOT)
    return qapp.exec()


def QSystemTrayAvailable() -> bool:  # noqa: N802
    from PySide6.QtWidgets import QSystemTrayIcon
    return QSystemTrayIcon.isSystemTrayAvailable()


if __name__ == "__main__":
    sys.exit(main())
