"""Вставка текста в активное окно (X11).

Основной способ — как у Aqua Voice: положить текст в буфер обмена, нажать
Ctrl+V (в терминалах Ctrl+Shift+V) через XTest и вернуть прежнее содержимое
буфера. Запасной — набор через xdotool type.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    from Xlib import X, XK, Xatom, display
    from Xlib.ext import xtest
except ImportError:  # pragma: no cover
    X = None

TERMINAL_CLASSES = {
    "gnome-terminal-server", "gnome-terminal", "kgx", "org.gnome.console", "ptyxis",
    "org.gnome.ptyxis", "tilix", "terminator", "kitty", "alacritty", "konsole",
    "xfce4-terminal", "mate-terminal", "lxterminal", "qterminal", "foot", "wezterm",
    "org.wezfurlong.wezterm", "guake", "tilda", "terminology", "cool-retro-term", "tabby",
    "hyper", "warp", "blackbox", "com.raggesilver.blackbox", "deepin-terminal", "st-256color",
    "sakura", "roxterm", "termite", "yakuake", "contour", "rio", "ghostty", "com.mitchellh.ghostty",
}
SHIFT_INSERT_CLASSES = {"xterm", "uxterm", "urxvt", "rxvt", "urxvt-unicode", "st"}


class WindowInfo:
    def __init__(self, wm_class: str = "", title: str = "", pid: Optional[int] = None, instance: str = ""):
        self.wm_class = wm_class
        self.instance = instance        # первая часть WM_CLASS (gnome-terminal-server), класс — вторая
        self.title = title
        self.pid = pid

    @property
    def names(self) -> set[str]:
        return {n.lower() for n in (self.wm_class, self.instance) if n}

    @property
    def app(self) -> str:
        return self.wm_class or "неизвестно"

    def __repr__(self) -> str:
        return f"WindowInfo({self.wm_class!r}, {self.title!r})"


def _connection_lost(exc: Exception) -> bool:
    """Ошибка — это оборванное соединение с X-сервером (а не, скажем, закрытое окно)?"""
    name = type(exc).__name__
    return isinstance(exc, (OSError, EOFError)) or "ConnectionClosed" in name or "DisplayConnection" in name


class X11Desktop:
    def __init__(self):
        self.dpy = display.Display() if X is not None else None
        if self.dpy is not None:
            self.root = self.dpy.screen().root
            self.NET_ACTIVE = self.dpy.intern_atom("_NET_ACTIVE_WINDOW")
            self.NET_NAME = self.dpy.intern_atom("_NET_WM_NAME")
            self.NET_PID = self.dpy.intern_atom("_NET_WM_PID")
            self.UTF8 = self.dpy.intern_atom("UTF8_STRING")

    # ---------------------------------------------------------- активное окно
    def active_window(self) -> WindowInfo:
        if self.dpy is None:
            return WindowInfo()
        for attempt in range(2):
            try:
                return self._active_window()
            except Exception as exc:  # noqa: BLE001
                if attempt == 0 and _connection_lost(exc):
                    log.warning("Соединение с X-сервером оборвалось — переподключаюсь")
                    self.reconnect()
                    continue
                log.debug("Не удалось определить активное окно", exc_info=True)
                return WindowInfo()
        return WindowInfo()

    def _active_window(self) -> WindowInfo:
        prop = self.root.get_full_property(self.NET_ACTIVE, Xatom.WINDOW)
        if prop and prop.value and prop.value[0]:
            win = self.dpy.create_resource_object("window", prop.value[0])
        else:
            # Нет _NET_ACTIVE_WINDOW (фокус на рабочем столе, редкие окна, без оконного
            # менеджера) — берём окно с фокусом клавиатуры и поднимаемся до окна с WM_CLASS.
            win = self._focus_toplevel()
            if win is None:
                return WindowInfo()
        wm_class = instance = ""
        cls = win.get_wm_class()
        if cls:
            instance = cls[0] or ""
            wm_class = cls[1] or cls[0] or ""
        title = ""
        name = win.get_full_property(self.NET_NAME, self.UTF8)
        if name and name.value:
            title = name.value.decode("utf-8", "replace") if isinstance(name.value, bytes) else str(name.value)
        else:
            title = win.get_wm_name() or ""
        pid_prop = win.get_full_property(self.NET_PID, Xatom.CARDINAL)
        pid = int(pid_prop.value[0]) if pid_prop and pid_prop.value else None
        return WindowInfo(wm_class, title, pid, instance)

    def _focus_toplevel(self):
        focus = self.dpy.get_input_focus().focus
        if not focus or isinstance(focus, int):
            return None
        win = focus
        for _ in range(12):
            try:
                if win.get_wm_class():
                    return win
                parent = win.query_tree().parent
            except Exception:  # noqa: BLE001
                return None
            if not parent or parent == self.root or parent.id == self.root.id:
                return None
            win = parent
        return None

    def reconnect(self) -> None:
        """Соединение с X-сервером оборвалось — открыть новое."""
        try:
            self.__init__()
        except Exception:  # noqa: BLE001
            log.warning("X-сервер пока недоступен", exc_info=True)

    # ---------------------------------------------------------- клавиатура
    def _keycode(self, name: str) -> int:
        keysym = XK.string_to_keysym(name)
        return self.dpy.keysym_to_keycode(keysym) if keysym else 0

    def send_combo(self, modifiers: list[str], key: str) -> bool:
        for attempt in range(2):
            try:
                return self._send_combo(modifiers, key)
            except Exception:  # noqa: BLE001 — оборвалось соединение с X: переподключаемся и повторяем
                log.warning("Не удалось отправить клавиши — переподключаюсь к X", exc_info=True)
                self.reconnect()
        return False

    def _send_combo(self, modifiers: list[str], key: str) -> bool:
        if self.dpy is None:
            return False
        codes = [self._keycode(m) for m in modifiers]
        key_code = self._keycode(key)
        if not key_code or not all(codes):
            return False
        for code in codes:
            xtest.fake_input(self.dpy, X.KeyPress, code)
        xtest.fake_input(self.dpy, X.KeyPress, key_code)
        xtest.fake_input(self.dpy, X.KeyRelease, key_code)
        for code in reversed(codes):
            xtest.fake_input(self.dpy, X.KeyRelease, code)
        self.dpy.sync()
        return True

    def dummy_keycode(self) -> int:
        """Свободный keycode без символов — «пустое» нажатие, которое приложения игнорируют."""
        if self.dpy is None:
            return 0
        cached = getattr(self, "_dummy", None)
        if cached is not None and not any(self.dpy.keycode_to_keysym(cached, i) for i in range(4)):
            return cached
        setup = self.dpy.display.info
        lo, hi = setup.min_keycode, setup.max_keycode
        for code in range(min(hi, 255), max(lo, 200) - 1, -1):
            if not any(self.dpy.keycode_to_keysym(code, i) for i in range(4)):
                self._dummy = code
                return code
        self._dummy = 0
        return 0

    def tap_keycode(self, code: int) -> None:
        for attempt in range(2):
            try:
                xtest.fake_input(self.dpy, X.KeyPress, code)
                xtest.fake_input(self.dpy, X.KeyRelease, code)
                self.dpy.sync()
                return
            except Exception as exc:  # noqa: BLE001
                if attempt or not _connection_lost(exc):
                    log.debug("Не удалось нажать keycode %s", code, exc_info=True)
                    return
                self.reconnect()

    def release_modifiers(self, names: list[str]) -> None:
        """Отпустить модификаторы, которые человек ещё держит (иначе Ctrl+V станет Super+Ctrl+V)."""
        if self.dpy is None:
            return
        for name in names:
            code = self._keycode(name)
            if code:
                xtest.fake_input(self.dpy, X.KeyRelease, code)
        self.dpy.sync()

    def paste_keys_for(self, window: WindowInfo, terminal_shift: bool = True) -> tuple[list[str], str]:
        names = window.names
        if names & SHIFT_INSERT_CLASSES:
            return ["Shift_L"], "Insert"
        if terminal_shift and (names & TERMINAL_CLASSES or any(n.endswith("terminal") for n in names)):
            return ["Control_L", "Shift_L"], "v"
        return ["Control_L"], "v"


def xdotool_type(text: str) -> bool:
    exe = shutil.which("xdotool")
    if not exe:
        return False
    try:
        subprocess.run([exe, "type", "--clearmodifiers", "--delay", "4", "--", text],
                       check=True, timeout=max(5, len(text) * 0.05))
        return True
    except Exception:  # noqa: BLE001
        log.exception("xdotool type не сработал")
        return False


def read_primary_selection(timeout: float = 0.4) -> str:
    """Выделенный текст (X11 PRIMARY) — для командного режима, без нажатия Ctrl+C."""
    for tool, args in (("xclip", ["-o", "-selection", "primary"]), ("xsel", ["-o", "-p"])):
        exe = shutil.which(tool)
        if exe:
            try:
                out = subprocess.run([exe, *args], capture_output=True, timeout=timeout)
                return out.stdout.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
    return ""


def wait_until(predicate, timeout: float, step: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
