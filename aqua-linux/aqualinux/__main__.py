"""Точка входа: aqua-linux [--background] | toggle | start | stop | cancel | paste-last | settings | quit."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque

from .config import APP_ID, APP_NAME, LOG_FILE, PROJECT_ROOT, STATE_DIR, Settings, ensure_dirs

COMMANDS = ("toggle", "start", "stop", "cancel", "paste-last", "show", "settings", "history", "quit")
RESTART_CODE = 75      # приложение само просит перезапуск


def setup_logging(verbose: bool) -> None:
    ensure_dirs()
    handlers = [logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr and sys.stderr.isatty() or verbose:
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


def supervise(child_args: list[str], verbose: bool) -> int:
    """«Сторож»: запускает приложение отдельным процессом и сам перезапускает его после сбоя.
    Нормальный выход (меню «Выйти», завершение сеанса) — код 0, тогда сторож тоже выходит."""
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
        proc = subprocess.Popen([sys.executable, "-m", "aqualinux", "--child", *args])
        state["proc"] = proc
        while True:
            try:
                rc = proc.wait()
                break
            except InterruptedError:
                continue
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
    try:
        import faulthandler
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(STATE_DIR / "crash.log", "a", encoding="utf-8"))   # noqa: SIM115
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} — локальная голосовая диктовка")
    parser.add_argument("command", nargs="?", choices=COMMANDS, help="команда уже запущенному приложению")
    parser.add_argument("--background", action="store_true", help="запуск без окна (автозапуск)")
    parser.add_argument("--no-supervisor", action="store_true", help="без автоматического перезапуска")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="store_true")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")   # нужен X11: XRecord/XTest/override-redirect
    os.environ["RESOURCE_NAME"] = APP_ID               # WM_CLASS=aqua-linux: значок в доке, Blur My Shell
    from .app import send_ipc

    # Уже запущен? Тогда просто передаём команду (или показываем окно).
    if send_ipc(args.command or ("noop" if args.background else "show")):
        return 0
    if args.command in ("stop", "cancel", "paste-last", "quit"):
        print(f"{APP_NAME} не запущен", file=sys.stderr)
        return 1
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
    tick = QTimer()
    tick.start(300)
    tick.timeout.connect(lambda: None)
    qapp.aboutToQuit.connect(app.shutdown)
    log.info("%s запущен (%s)", APP_NAME, PROJECT_ROOT)
    return qapp.exec()


def QSystemTrayAvailable() -> bool:  # noqa: N802
    from PySide6.QtWidgets import QSystemTrayIcon
    return QSystemTrayIcon.isSystemTrayAvailable()


if __name__ == "__main__":
    sys.exit(main())
