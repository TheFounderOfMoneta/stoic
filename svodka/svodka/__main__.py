"""Точка входа: python -m svodka [команда].

  (без команды) / gui      — окно приложения
  collect [--force]        — сбор сейчас (--scheduled — запуск от таймера)
  search "запрос"          — поиск в интернете с Claude
  translate ID             — перевести статью
  mcp                      — MCP-сервер для Claude (запускает сам Claude)
  install-timer            — поставить/обновить таймер systemd
  import-takeout ПУТЬ      — стартовые интересы из Google Takeout (YouTube, поиск)
  weekly                   — еженедельный разбор профиля Claude
  status                   — состояние: вход в Claude, последний сбор, таймер
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _storage_settings():
    from .config import DB_FILE, Settings, ensure_dirs, setup_logging
    from .storage import Storage
    setup_logging()
    ensure_dirs()
    return Storage(DB_FILE), Settings()


def gui_running() -> bool:
    from .config import STATE_DIR
    pid_file = STATE_DIR / "gui.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="svodka", description="Сводка — личный поисковик новостей")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("gui")
    c = sub.add_parser("collect")
    c.add_argument("--force", action="store_true")
    c.add_argument("--scheduled", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("query")
    t = sub.add_parser("translate")
    t.add_argument("article_id", type=int)
    sub.add_parser("mcp")
    sub.add_parser("install-timer")
    imp = sub.add_parser("import-takeout")
    imp.add_argument("path")
    sub.add_parser("weekly")
    sub.add_parser("status")
    args = parser.parse_args(argv)

    if args.cmd in (None, "gui"):
        from .app import main as gui_main
        return gui_main()
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0

    storage, settings = _storage_settings()
    if args.cmd == "collect":
        from . import collect
        if args.scheduled and not settings.get("schedule.enabled", True):
            return 0
        res = collect.run(settings, storage, force=args.force, on_progress=lambda m: print(m, flush=True))
        print(res["message"])
        if args.scheduled and res["saved"] and not gui_running() and settings.get("ui.notifications", True):
            collect.notify("Сводка", f"Свежих статей: {res['saved']}")
        return 0 if res["status"] in ("ok", "empty", "skipped", "busy", "partial") else 1
    if args.cmd == "search":
        from . import collect
        res = collect.run(settings, storage, kind="search", query=args.query, force=True,
                          on_progress=lambda m: print(m, flush=True))
        print(res["message"])
        return 0 if res["status"] != "failed" else 1
    if args.cmd == "translate":
        from . import translate
        res = translate.translate_article(settings, storage, args.article_id,
                                          on_progress=lambda m: print(m, flush=True))
        print(res["message"])
        return 0 if res["status"] in ("done", "skipped") else 1
    if args.cmd == "install-timer":
        from . import systemd
        ok, msg = systemd.install(settings.get("schedule.times", ["07:37", "18:37"]),
                                  enable=settings.get("schedule.enabled", True))
        print("Таймер установлен." if ok else f"Не получилось: {msg}")
        return 0 if ok else 1
    if args.cmd == "import-takeout":
        from . import takeout
        res = takeout.import_takeout(settings, storage, args.path, on_progress=lambda m: print(m, flush=True))
        print(res["message"])
        return 0 if res["ok"] else 1
    if args.cmd == "weekly":
        from . import weekly
        res = weekly.run(settings, storage, force=True)
        print(res["message"])
        return 0
    if args.cmd == "status":
        from . import claude_cli, systemd
        st = claude_cli.auth_status(settings.get("claude.command", "claude"))
        print("Claude:", "вход выполнен" if st["ok"] else st["message"])
        last = storage.last_run("collect", ok_only=False)
        if last:
            print("Последний сбор:", time.strftime("%d.%m %H:%M", time.localtime(last["started"])),
                  last["status"], f"сохранено {last['saved']}")
        print("Таймер:", systemd.next_run() or "не установлен")
        n = storage.one("SELECT count(*) AS n FROM articles WHERE purged=0")["n"]
        print("Статей в ленте:", n, "· действий для обучения:", storage.interactions_count())
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
