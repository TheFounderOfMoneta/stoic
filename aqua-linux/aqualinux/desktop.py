"""Интеграция с GNOME: нижний док (dash-to-dock / Ubuntu Dock) и Blur My Shell."""
from __future__ import annotations

import ast
import logging
import shutil
import subprocess
from functools import lru_cache

log = logging.getLogger(__name__)

WM_CLASS = "aqua-linux"
DOCK = "/org/gnome/shell/extensions/dash-to-dock/"
BLUR_APPS = "/org/gnome/shell/extensions/blur-my-shell/applications/"


def _dconf_read(key: str):
    if not shutil.which("dconf"):
        return None
    try:
        out = subprocess.run(["dconf", "read", key], capture_output=True, text=True, timeout=1).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not out:
        return None
    if out.startswith("@as "):
        out = out[4:]
    try:
        return ast.literal_eval(out.replace("true", "True").replace("false", "False"))
    except (ValueError, SyntaxError):
        return out.strip("'")


def _dconf_write(key: str, value: str) -> bool:
    try:
        subprocess.run(["dconf", "write", key, value], check=True, timeout=2)
        return True
    except Exception:  # noqa: BLE001
        log.exception("dconf write %s не удалась", key)
        return False


@lru_cache(maxsize=1)
def dock_info() -> dict:
    position = str(_dconf_read(DOCK + "dock-position") or "").upper()
    if not position:
        # Значение по умолчанию не пишется в dconf: у Dash to Dock это низ, у Ubuntu Dock — слева.
        enabled = _run(["gnome-extensions", "list", "--enabled"])
        if "dash-to-dock@micxgx.gmail.com" in enabled:
            position = "BOTTOM"
        elif "ubuntu-dock@ubuntu.com" in enabled:
            position = "LEFT"
        else:
            position = "UNKNOWN"
    icon = _dconf_read(DOCK + "dash-max-icon-size")
    return {"position": position, "icon": int(icon) if isinstance(icon, int) else 48}


def bubble_bottom_margin() -> int:
    """Отступ плавающей панели от низа экрана: всегда над нижним доком (как Aqua над Dock в macOS).
    Если док слева/справа — ниже; если определить не удалось — с запасом над типичным доком."""
    info = dock_info()
    if info["position"] in ("LEFT", "RIGHT"):
        return 26
    return max(96, info["icon"] + 48)


def blur_my_shell_installed() -> bool:
    return _dconf_read("/org/gnome/shell/extensions/blur-my-shell/settings-version") is not None \
        or _dconf_read(BLUR_APPS + "blur") is not None \
        or bool(shutil.which("gnome-extensions") and "blur-my-shell" in _run(["gnome-extensions", "list"]))


def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout
    except Exception:  # noqa: BLE001
        return ""


def blur_enabled_for_app() -> bool:
    whitelist = _dconf_read(BLUR_APPS + "whitelist") or []
    return bool(_dconf_read(BLUR_APPS + "blur")) and WM_CLASS in whitelist


def enable_blur_for_app(enable: bool = True) -> bool:
    """Добавить окно Aqua Linux в белый список размытия приложений Blur My Shell."""
    whitelist = list(_dconf_read(BLUR_APPS + "whitelist") or [])
    if enable and WM_CLASS not in whitelist:
        whitelist.append(WM_CLASS)
    if not enable and WM_CLASS in whitelist:
        whitelist.remove(WM_CLASS)
    value = "[" + ", ".join("'" + w.replace("'", "") + "'" for w in whitelist) + "]"
    ok = _dconf_write(BLUR_APPS + "whitelist", value if whitelist else "@as []")
    if enable:
        ok = _dconf_write(BLUR_APPS + "blur", "true") and ok
    return ok
