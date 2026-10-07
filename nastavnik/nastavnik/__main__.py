"""Точка входа: python -m nastavnik [команда].

  (без команды) / gui      — окно приложения (--background — свёрнутым в значок панели)
  mcp                      — MCP-сервер для Claude-репетитора (запускает сам Claude)
  remind [--scheduled]     — напомнить об учёбе, если сегодня ещё не занимались
  install-timer            — поставить/обновить таймер напоминания systemd
  remove-timer             — убрать таймер
  install-desktop          — ярлык в меню приложений и значок
  remove-desktop           — убрать ярлык, значок и автозапуск
  status                   — состояние: вход в Claude, серия, повторения, таймер
  export ФАЙЛ              — всё об учёбе в JSON
"""
from __future__ import annotations

import argparse
import sys


def _storage_settings():
    from .config import DB_FILE, Settings, ensure_dirs, setup_logging
    from .storage import Storage
    setup_logging()
    ensure_dirs()
    return Storage(DB_FILE), Settings()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nastavnik", description="Наставник — учёба с Claude под вас")
    sub = parser.add_subparsers(dest="cmd")
    g = sub.add_parser("gui")
    g.add_argument("--background", action="store_true")
    sub.add_parser("mcp")
    r = sub.add_parser("remind")
    r.add_argument("--scheduled", action="store_true")
    sub.add_parser("install-timer")
    sub.add_parser("remove-timer")
    sub.add_parser("install-desktop")
    sub.add_parser("remove-desktop")
    sub.add_parser("status")
    e = sub.add_parser("export")
    e.add_argument("path")
    args = parser.parse_args(argv)

    if args.cmd in (None, "gui"):
        from .app import main as gui_main
        return gui_main(["--background"] if getattr(args, "background", False) else [])
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0
    if args.cmd == "install-desktop":
        from . import desktop
        for path in desktop.install():
            print(path)
        return 0
    if args.cmd == "remove-desktop":
        from . import desktop
        desktop.remove()
        return 0
    if args.cmd == "remove-timer":
        from . import systemd
        systemd.remove()
        return 0

    storage, settings = _storage_settings()
    if args.cmd == "remind":
        from . import systemd
        if args.scheduled and not settings.get("reminder.enabled", True):
            return 0
        res = systemd.remind(storage, settings)
        print(res.get("title") or res.get("reason", ""))
        return 0
    if args.cmd == "install-timer":
        from . import systemd
        ok, msg = systemd.install(settings.get("reminder.time", "19:07"),
                                  enable=settings.get("reminder.enabled", True))
        print("Таймер напоминания установлен." if ok else f"Не получилось: {msg}")
        return 0 if ok else 1
    if args.cmd == "status":
        from . import claude_cli
        from .learn import engine, metrics, planner
        from .util import now
        st = claude_cli.auth_status(settings.get("claude.command", "claude"))
        print("Claude:", "вход выполнен" if st["ok"] else st["message"])
        s = metrics.streak(storage, now(), int(settings.get("learn.streak_freezes_per_week", 1)))
        print(f"Серия: {s['days']} дн.", "(сегодня уже занимались)" if s["studied_today"] else "")
        print(f"За неделю: {round(metrics.week_minutes(storage, now()))} мин из "
              f"{settings.get('learn.week_goal_minutes', 180)}")
        plan = planner.plan_today(storage, settings, None)
        print("Повторений на сегодня:", plan.due_total)
        r = engine.retention_all(storage)
        if r is not None:
            print(f"Прогноз удержания: {round(r * 100)} %")
        print("Тем:", len(storage.topics()))
        return 0
    if args.cmd == "export":
        from .tutor import export_json
        with open(args.path, "w", encoding="utf-8") as f:
            f.write(export_json(storage))
        print(args.path)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
