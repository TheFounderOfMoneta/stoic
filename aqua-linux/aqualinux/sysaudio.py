"""Звук системы во время диктовки: приглушить вывод или поставить плееры на паузу."""
from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import subprocess
import threading

from .config import STATE_DIR

log = logging.getLogger(__name__)
# Что Aqua выключила/поставила на паузу. Если приложение упадёт посреди диктовки, при
# следующем запуске звук вернётся сам (иначе музыка осталась бы выключенной навсегда).
STATE_FILE = STATE_DIR / "muted-by-aqua.json"


def _run(args: list[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ""


class SystemAudio:
    """Команды выполняются в одном фоновом потоке строго по порядку: «вернуть звук» никогда
    не обгонит «выключить звук» (иначе после короткой диктовки музыка осталась бы выключенной)."""

    def __init__(self):
        self._muted_by_us = False
        self._paused_players: list[str] = []
        self._wpctl = shutil.which("wpctl")
        self._pactl = shutil.which("pactl")
        self._gdbus = shutil.which("gdbus")
        self._queue: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="sysaudio", daemon=True)
        self._worker.start()
        self._queue.put(self._recover)

    def _run(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.debug("sysaudio", exc_info=True)
            finally:
                self._queue.task_done()

    def _save_state(self) -> None:
        try:
            if self._muted_by_us or self._paused_players:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                STATE_FILE.write_text(json.dumps({"muted": self._muted_by_us, "paused": self._paused_players}))
            elif STATE_FILE.exists():
                STATE_FILE.unlink()
        except OSError:
            pass

    def _recover(self) -> None:
        """Прошлый запуск завершился посреди диктовки — вернуть звук и плееры."""
        try:
            data = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        log.warning("Прошлый запуск не вернул звук системы — возвращаю")
        if isinstance(data, dict):
            if data.get("muted"):
                self._set_mute(False)
            for player in data.get("paused") or []:
                if isinstance(player, str) and self._gdbus:
                    self._player_call(player, "Play")
        self._muted_by_us, self._paused_players = False, []
        self._save_state()

    def wait_idle(self, timeout: float = 3.0) -> None:
        import time
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    # --------------------------------------------------------------- mute
    def _is_muted(self) -> bool | None:
        if self._wpctl:
            out = _run([self._wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if out:
                return "[MUTED]" in out
        if self._pactl:
            out = _run([self._pactl, "get-sink-mute", "@DEFAULT_SINK@"])
            if out:
                return "yes" in out.lower() or "да" in out.lower()
        return None

    def _set_mute(self, mute: bool) -> None:
        if self._wpctl:
            _run([self._wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
        elif self._pactl:
            _run([self._pactl, "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])

    # -------------------------------------------------------------- MPRIS
    def _players(self) -> list[str]:
        if not self._gdbus:
            return []
        out = _run([self._gdbus, "call", "--session", "--dest", "org.freedesktop.DBus",
                    "--object-path", "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.ListNames"])
        return re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", out)

    def _status(self, player: str) -> str:
        out = _run([self._gdbus, "call", "--session", "--dest", player, "--object-path",
                    "/org/mpris/MediaPlayer2", "--method", "org.freedesktop.DBus.Properties.Get",
                    "org.mpris.MediaPlayer2.Player", "PlaybackStatus"])
        match = re.search(r"'(\w+)'", out)
        return match.group(1) if match else ""

    def _player_call(self, player: str, method: str) -> None:
        _run([self._gdbus, "call", "--session", "--dest", player, "--object-path",
              "/org/mpris/MediaPlayer2", "--method", f"org.mpris.MediaPlayer2.Player.{method}"])

    # ---------------------------------------------------------------- API
    def begin(self, mode: str) -> None:
        """mode: none | mute | pause. Выполняется в фоне, чтобы не тормозить старт записи."""
        if mode == "none":
            return

        def work():
            if mode == "mute":
                if not self._muted_by_us and self._is_muted() is False:
                    self._muted_by_us = True
                    self._save_state()          # сначала запоминаем — потом выключаем
                    self._set_mute(True)
            elif mode == "pause":
                for player in self._players():
                    if player not in self._paused_players and self._status(player) == "Playing":
                        self._paused_players.append(player)
                        self._save_state()
                        self._player_call(player, "Pause")

        self._queue.put(work)

    def end(self) -> None:
        def work():
            if self._muted_by_us:
                self._set_mute(False)
                self._muted_by_us = False
            for player in self._paused_players:
                self._player_call(player, "Play")
            self._paused_players = []
            self._save_state()

        self._queue.put(work)
