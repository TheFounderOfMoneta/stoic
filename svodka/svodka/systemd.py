"""Таймер systemd --user для сбора по расписанию (Persistent=true — догоняет после включения ПК)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .claude_cli import find_claude
from .config import PROJECT_ROOT

UNIT_DIR = Path.home() / ".config/systemd/user"
SERVICE = "svodka-collect.service"
TIMER = "svodka-collect.timer"


def service_text(python: str | None = None) -> str:
    python = python or sys.executable
    claude = find_claude() or "claude"
    path = ":".join(dict.fromkeys([str(Path(claude).parent), str(Path(python).parent),
                                   "/usr/local/bin", "/usr/bin", "/bin"]))
    return f"""[Unit]
Description=Сводка: сбор свежих новостей через Claude
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={PROJECT_ROOT}
Environment=PYTHONPATH={PROJECT_ROOT}
Environment=PATH={path}
ExecStart={python} -m svodka collect --scheduled
Nice=10
TimeoutStartSec=40min
"""


def timer_text(times: list[str]) -> str:
    cal = "\n".join(f"OnCalendar=*-*-* {t}:00" for t in times) or "OnCalendar=*-*-* 07:37:00"
    return f"""[Unit]
Description=Сводка: расписание сбора

[Timer]
{cal}
Persistent=true
RandomizedDelaySec=90

[Install]
WantedBy=timers.target
"""


def _systemctl(*args: str) -> tuple[bool, str]:
    exe = shutil.which("systemctl")
    if not exe:
        return False, "systemctl не найден"
    try:
        out = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=20)
        return out.returncode == 0, (out.stderr or out.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def install(times: list[str], enable: bool = True) -> tuple[bool, str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / SERVICE).write_text(service_text(), encoding="utf-8")
    (UNIT_DIR / TIMER).write_text(timer_text(times), encoding="utf-8")
    ok, msg = _systemctl("daemon-reload")
    if not ok:
        return False, msg
    if enable:
        return _systemctl("enable", "--now", TIMER)
    return _systemctl("disable", "--now", TIMER)


def remove() -> None:
    _systemctl("disable", "--now", TIMER)
    for name in (SERVICE, TIMER):
        try:
            (UNIT_DIR / name).unlink()
        except OSError:
            pass
    _systemctl("daemon-reload")


def next_run() -> str:
    ok, out = _systemctl("list-timers", TIMER, "--no-legend")
    return out.split("  ")[0].strip() if ok and out else ""


def ensure(times: list[str], enabled: bool) -> tuple[bool, str]:
    """Самопочинка: поставить/обновить таймер, только если он отличается от нужного.

    (True, "") — всё в порядке или systemd недоступен (тогда сбор догоняет само окно)."""
    if not shutil.which("systemctl"):
        return True, ""
    service, timer = UNIT_DIR / SERVICE, UNIT_DIR / TIMER
    same = (service.exists() and timer.exists()
            and service.read_text(encoding="utf-8") == service_text()
            and timer.read_text(encoding="utf-8") == timer_text(times))
    if not enabled:
        if timer.exists():
            return _systemctl("disable", "--now", TIMER)
        return True, ""
    if same:
        active, _ = _systemctl("is-enabled", TIMER)
        if active:
            return True, ""
    return install(times, enable=True)
