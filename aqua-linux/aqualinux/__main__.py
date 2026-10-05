"""Точка входа: aqua-linux [--background] | toggle | start | stop | cancel | paste-last | settings | quit."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys

from .config import APP_ID, APP_NAME, LOG_FILE, PROJECT_ROOT, Settings, ensure_dirs

COMMANDS = ("toggle", "start", "stop", "cancel", "paste-last", "show", "settings", "history", "quit")


def setup_logging(verbose: bool) -> None:
    ensure_dirs()
    handlers = [logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr and sys.stderr.isatty() or verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} — локальная голосовая диктовка")
    parser.add_argument("command", nargs="?", choices=COMMANDS, help="команда уже запущенному приложению")
    parser.add_argument("--background", action="store_true", help="запуск без окна (автозапуск)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")   # нужен X11: XRecord/XTest/override-redirect
    os.environ["RESOURCE_NAME"] = APP_ID               # WM_CLASS=aqua-linux: значок в доке, Blur My Shell
    from .app import send_ipc

    # Уже запущен? Тогда просто передаём команду (или показываем окно).
    if send_ipc(args.command or ("noop" if args.background else "show")):
        return 0
    if args.command in ("stop", "cancel", "paste-last", "quit"):
        print(f"{APP_NAME} не запущен", file=sys.stderr)
        return 1

    from PySide6.QtCore import Qt
    setup_logging(args.verbose)
    log = logging.getLogger(APP_ID)
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
