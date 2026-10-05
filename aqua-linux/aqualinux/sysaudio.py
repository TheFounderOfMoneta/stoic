"""Звук системы во время диктовки: приглушить вывод или поставить плееры на паузу."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)


def _run(args: list[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ""


class SystemAudio:
    def __init__(self):
        self._lock = threading.Lock()
        self._muted_by_us = False
        self._paused_players: list[str] = []
        self._wpctl = shutil.which("wpctl")
        self._pactl = shutil.which("pactl")
        self._gdbus = shutil.which("gdbus")

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
            with self._lock:
                if mode == "mute":
                    if self._is_muted() is False:
                        self._set_mute(True)
                        self._muted_by_us = True
                elif mode == "pause":
                    for player in self._players():
                        if self._status(player) == "Playing":
                            self._player_call(player, "Pause")
                            self._paused_players.append(player)

        threading.Thread(target=work, daemon=True).start()

    def end(self) -> None:
        def work():
            with self._lock:
                if self._muted_by_us:
                    self._set_mute(False)
                    self._muted_by_us = False
                for player in self._paused_players:
                    self._player_call(player, "Play")
                self._paused_players = []

        threading.Thread(target=work, daemon=True).start()
