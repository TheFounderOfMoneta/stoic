"""Напоминание по расписанию: таймер systemd --user (Persistent=true — догоняет после включения ПК).

Напоминание приходит, только если сегодня ещё не занимались, и использует открытую петлю
прошлой сессии как крючок: «Вчера остановились на: … · Сегодня ~25 мин».
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import APP_NAME, PROJECT_ROOT

UNIT_DIR = Path.home() / ".config/systemd/user"
SERVICE = "nastavnik-remind.service"
TIMER = "nastavnik-remind.timer"


def service_text(python: str | None = None) -> str:
    python = python or sys.executable
    path = ":".join(dict.fromkeys([str(Path(python).parent), "/usr/local/bin", "/usr/bin", "/bin"]))
    return f"""[Unit]
Description={APP_NAME}: напоминание об учёбе

[Service]
Type=oneshot
WorkingDirectory={PROJECT_ROOT}
Environment=PYTHONPATH={PROJECT_ROOT}
Environment=PATH={path}
ExecStart={python} -m nastavnik remind --scheduled
"""


def timer_text(time_hm: str) -> str:
    return f"""[Unit]
Description={APP_NAME}: расписание напоминания

[Timer]
OnCalendar=*-*-* {time_hm}:00
Persistent=true
RandomizedDelaySec=60

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


def install(time_hm: str, enable: bool = True) -> tuple[bool, str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (UNIT_DIR / SERVICE).write_text(service_text(), encoding="utf-8")
    (UNIT_DIR / TIMER).write_text(timer_text(time_hm), encoding="utf-8")
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


def ensure(time_hm: str, enabled: bool) -> tuple[bool, str]:
    """Самопочинка: поставить/обновить таймер, только если он отличается от нужного.

    (True, "") — всё в порядке или systemd недоступен (тогда напоминает само окно)."""
    if not shutil.which("systemctl"):
        return True, ""
    service, timer = UNIT_DIR / SERVICE, UNIT_DIR / TIMER
    same = (service.exists() and timer.exists()
            and service.read_text(encoding="utf-8") == service_text()
            and timer.read_text(encoding="utf-8") == timer_text(time_hm))
    if not enabled:
        if timer.exists():
            return _systemctl("disable", "--now", TIMER)
        return True, ""
    if same:
        active, _ = _systemctl("is-enabled", TIMER)
        if active:
            return True, ""
    return install(time_hm, enable=True)


def notify(title: str, body: str) -> bool:
    """Уведомление рабочего стола."""
    exe = shutil.which("notify-send")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-a", APP_NAME, "-i", "accessories-dictionary", title, body], timeout=5, check=False)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def remind(storage, settings, ts: float | None = None) -> dict:
    """Напомнить, если сегодня ещё не занимались. Напоминание записывается: по нему считается тяга."""
    from .learn import metrics, planner
    from .util import now
    ts = ts or now()
    st = metrics.streak(storage, ts, int(settings.get("learn.streak_freezes_per_week", 1)))
    if st["studied_today"]:
        return {"sent": False, "reason": "сегодня уже занимались"}
    topics = storage.topics()
    topic_id = topics[-1]["id"] if topics else None
    last = storage.last_checkpoint()
    if last:
        topic_id = last["topic_id"]
    plan = planner.plan_today(storage, settings, topic_id, ts)
    if plan.kind == "return":
        title = "Пять минут, чтобы вернуться"
    elif last and last.get("open_loop"):
        title = "Вы остановились на вопросе"
    else:
        title = "Время учиться"
    lines = []
    if last and last.get("open_loop"):
        lines.append(f"«{last['open_loop']}»")
    lines.append(f"Сегодня ~{max(5, round(plan.minutes or plan.budget))} мин"
                 + (f" · серия {st['days']} дн." if st["days"] >= 2 else ""))
    body = "\n".join(lines)
    sent = notify(title, body)
    storage.log_event("reminder", meta=title, ts=ts)
    return {"sent": sent, "title": title, "body": body}
