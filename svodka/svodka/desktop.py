"""Ярлык в меню приложений и значок (ставит install.sh, убирает uninstall.sh)."""
from __future__ import annotations

import os
from pathlib import Path

from .config import APP_ID, DATA_DIR

SHARE = DATA_DIR.parent                         # ~/.local/share
DESKTOP_FILE = SHARE / "applications" / f"{APP_ID}.desktop"
ICON_SIZES = (48, 64, 128, 256)


def icon_path(size: int) -> Path:
    return SHARE / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"


def install() -> list[Path]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    _app = QGuiApplication.instance() or QGuiApplication([])     # без него Qt не рисует
    from .app import desktop_entry
    from .ui.look import app_icon_pixmap
    written = []
    for size in ICON_SIZES:
        path = icon_path(size)
        path.parent.mkdir(parents=True, exist_ok=True)
        app_icon_pixmap(size).save(str(path), "PNG")
        written.append(path)
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(desktop_entry(["gui"]), encoding="utf-8")
    written.append(DESKTOP_FILE)
    return written


def remove() -> None:
    for path in [DESKTOP_FILE] + [icon_path(s) for s in ICON_SIZES]:
        try:
            path.unlink()
        except OSError:
            pass
