"""Автозапуск через ~/.config/autostart (стандарт XDG)."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from .config import APP_ID, APP_NAME, PROJECT_ROOT

AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / f"{APP_ID}.desktop"


def launcher() -> str:
    local = Path.home() / ".local/bin" / APP_ID
    if local.exists():
        return str(local)
    return str(PROJECT_ROOT / "run.sh")


def desktop_entry(background: bool) -> str:
    exec_line = shlex.quote(launcher()) + (" --background" if background else "")
    return f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Локальная голосовая диктовка (GigaAM)
Exec={exec_line}
Icon={APP_ID}
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass=aqua-linux
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
"""


def set_autostart(enabled: bool) -> None:
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(desktop_entry(background=True), encoding="utf-8")
    elif AUTOSTART.exists():
        AUTOSTART.unlink()
