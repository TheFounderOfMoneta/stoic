"""Глобальные горячие клавиши для X11 (XRecord + XGrabKey).

XRecord видит все нажатия и отпускания клавиш без прав root — это нужно для
режима «удерживай и говори». Клавиши определяются по латинской раскладке
(группа 0), поэтому сочетания работают и при включённой русской раскладке.

XGrabKey используется только для подавления сочетаний с обычной клавишей
(например Ctrl+Super+V или Escape во время записи), чтобы они не доходили до
активного приложения. Чисто модификаторные сочетания (Ctrl+Super) не
перехватываются — X11 этого не позволяет без поломки модификаторов.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

try:
    from Xlib import X, XK, display
    from Xlib.ext import record, xfixes
    from Xlib.protocol import rq
except ImportError:  # pragma: no cover
    X = None

_KEYSYM_NAMES: dict[int, str] = {}


def keysym_name(keysym: int) -> str:
    """Имя keysym («Control_L», «space», «v», «F9»)."""
    if not _KEYSYM_NAMES and X is not None:
        for group in ("miscellany", "latin1", "xkb", "xf86"):
            try:
                XK.load_keysym_group(group)
            except Exception:  # noqa: BLE001
                pass
        for attr in sorted(dir(XK)):
            if attr.startswith("XK_"):
                _KEYSYM_NAMES.setdefault(getattr(XK, attr), attr[3:])
    return _KEYSYM_NAMES.get(keysym, "")

MODIFIER_TOKENS = {
    "Control_L": ("ctrl", "lctrl"), "Control_R": ("ctrl", "rctrl"),
    "Shift_L": ("shift", "lshift"), "Shift_R": ("shift", "rshift"),
    "Alt_L": ("alt", "lalt"), "Alt_R": ("alt", "ralt"), "ISO_Level3_Shift": ("alt", "ralt"),
    "Meta_L": ("alt", "lalt"), "Meta_R": ("alt", "ralt"),
    "Super_L": ("super", "lsuper"), "Super_R": ("super", "rsuper"),
    "Hyper_L": ("super", "lsuper"), "Hyper_R": ("super", "rsuper"),
}
MODIFIER_GENERIC = {"ctrl", "shift", "alt", "super"}

PRETTY = {
    "ctrl": "Ctrl", "lctrl": "Левый Ctrl", "rctrl": "Правый Ctrl",
    "shift": "Shift", "lshift": "Левый Shift", "rshift": "Правый Shift",
    "alt": "Alt", "lalt": "Левый Alt", "ralt": "Правый Alt",
    "super": "Super", "lsuper": "Левый Super", "rsuper": "Правый Super",
    "space": "Пробел", "escape": "Esc", "return": "Enter", "tab": "Tab",
    "backspace": "Backspace", "menu": "Menu", "insert": "Insert", "pause": "Pause",
    "scroll_lock": "Scroll Lock", "caps_lock": "Caps Lock",
}


def pretty_token(token: str) -> str:
    if token in PRETTY:
        return PRETTY[token]
    if token.startswith("mouse"):
        return f"Кнопка мыши {token[5:]}"
    if token.startswith("f") and token[1:].isdigit():
        return token.upper()
    return token.upper() if len(token) == 1 else token.capitalize()


def pretty_combo(combo: Iterable[str]) -> str:
    combo = list(combo or [])
    return " + ".join(pretty_token(t) for t in combo) if combo else "не задано"


def _keysym_token(name: str) -> str:
    return name.lower()


class KeyState:
    """Нажатые клавиши: имя keysym → (токены)."""

    def __init__(self):
        self.down: dict[str, tuple[str, ...]] = {}

    def press(self, name: str) -> bool:
        if name in self.down:
            return False  # автоповтор
        self.down[name] = MODIFIER_TOKENS.get(name, (_keysym_token(name),))
        return True

    def release(self, name: str) -> bool:
        return self.down.pop(name, None) is not None

    def matches(self, combo: Iterable[str]) -> bool:
        """Нажато ровно это сочетание (лишних клавиш нет)."""
        combo = list(combo or [])
        if not combo:
            return False
        keys = list(self.down.values())
        if len(keys) != len(combo):
            return False
        used = [False] * len(keys)
        for token in combo:
            for i, aliases in enumerate(keys):
                if not used[i] and token in aliases:
                    used[i] = True
                    break
            else:
                return False
        return True

    def contains(self, combo: Iterable[str]) -> bool:
        """Все клавиши сочетания нажаты (могут быть и другие)."""
        keys = list(self.down.values())
        used = [False] * len(keys)
        for token in combo or []:
            for i, aliases in enumerate(keys):
                if not used[i] and token in aliases:
                    used[i] = True
                    break
            else:
                return False
        return bool(combo)

    def any_modifier(self) -> bool:
        return any(aliases[0] in MODIFIER_GENERIC for aliases in self.down.values())

    def tokens(self) -> list[str]:
        return [aliases[1] if len(aliases) > 1 else aliases[0] for aliases in self.down.values()]


class X11KeyListener:
    """Слушатель XRecord. on_event(kind, name, t) вызывается из потока диспетчера.

    kind: "press" | "release"; name — имя keysym («Control_L», «space», «v») или «mouse8».
    """

    def __init__(self, on_event: Callable[[str, str, float], None]):
        if X is None:
            raise RuntimeError("Не установлен python-xlib")
        self.on_event = on_event
        self._raw: "queue.Queue[tuple]" = queue.Queue()
        self._rec_dpy = None
        self._ctx = None
        self._local = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    def start(self) -> None:
        self._local = display.Display()
        self._rec_dpy = display.Display()
        if not self._rec_dpy.has_extension("RECORD"):
            raise RuntimeError("X-сервер не поддерживает расширение RECORD")
        self._ctx = self._rec_dpy.record_create_context(
            0, [record.AllClients],
            [{
                "core_requests": (0, 0), "core_replies": (0, 0),
                "ext_requests": (0, 0, 0, 0), "ext_replies": (0, 0, 0, 0),
                "delivered_events": (0, 0),
                "device_events": (X.KeyPress, X.ButtonRelease),
                "errors": (0, 0), "client_started": False, "client_died": False,
            }])
        for target, name in ((self._record_loop, "xrecord"), (self._dispatch_loop, "keys")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ctx is not None and self._local is not None:
                self._local.record_disable_context(self._ctx)
                self._local.flush()
        except Exception:  # noqa: BLE001
            pass
        self._raw.put(None)

    def _record_loop(self) -> None:
        try:
            self._rec_dpy.record_enable_context(self._ctx, self._on_reply)
            self._rec_dpy.record_free_context(self._ctx)
        except Exception:  # noqa: BLE001
            if not self._stop.is_set():
                log.exception("XRecord остановился")

    def _on_reply(self, reply) -> None:
        if reply.category != record.FromServer or reply.client_swapped:
            return
        data = reply.data
        if not data or data[0] < 2:
            return
        while data:
            event, data = rq.EventField(None).parse_binary_value(data, self._rec_dpy.display, None, None)
            if event.type in (X.KeyPress, X.KeyRelease):
                keysym = self._local.keycode_to_keysym(event.detail, 0)
                name = keysym_name(keysym) if keysym else None
                if not name:
                    name = f"keycode{event.detail}"
                kind = "press" if event.type == X.KeyPress else "release"
            elif event.type in (X.ButtonPress, X.ButtonRelease):
                if event.detail < 8:  # 1-3 клики, 4-7 колесо — не трогаем
                    continue
                name = f"mouse{event.detail}"
                kind = "press" if event.type == X.ButtonPress else "release"
            else:
                continue
            self._raw.put((kind, name, event.time))

    def _dispatch_loop(self) -> None:
        """Отсекаем автоповтор: release+press одной клавиши с тем же временем."""
        held_release = None
        while not self._stop.is_set():
            try:
                item = self._raw.get(timeout=0.03 if held_release else None)
            except queue.Empty:
                item = "timeout"
            if item is None:
                return
            if held_release is not None:
                if item != "timeout" and item[0] == "press" and item[1] == held_release[1] \
                        and abs(item[2] - held_release[2]) <= 2:
                    held_release = None
                    continue
                self._emit(*held_release)
                held_release = None
            if item == "timeout":
                continue
            if item[0] == "release":
                held_release = item
            else:
                self._emit(*item)

    def _emit(self, kind: str, name: str, _server_time) -> None:
        try:
            self.on_event(kind, name, time.monotonic())
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обработчика клавиш")


class X11Grabber:
    """Пассивный захват сочетаний, чтобы они не уходили в приложения."""

    MOD_MASKS = {"ctrl": "ControlMask", "shift": "ShiftMask", "alt": "Mod1Mask", "super": "Mod4Mask"}

    def __init__(self):
        self._dpy = display.Display() if X is not None else None
        self._grabs: dict[tuple, list] = {}
        self._lock = threading.Lock()
        self._errors: list = []
        if self._dpy is not None:
            self._root = self._dpy.screen().root
            # Поток, который просто вычитывает события, чтобы очередь не росла.
            threading.Thread(target=self._drain, name="xgrab", daemon=True).start()

    def _drain(self) -> None:
        while True:
            try:
                self._dpy.next_event()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

    def _combo_to_grab(self, combo):
        mods = 0
        key = None
        for token in combo:
            generic = token.lstrip("lr") if token[:1] in "lr" and token[1:] in MODIFIER_GENERIC else token
            if generic in self.MOD_MASKS:
                mods |= getattr(X, self.MOD_MASKS[generic])
            elif token.startswith("mouse"):
                return None
            else:
                if key is not None:
                    return None
                key = token
        if key is None:
            return None
        name = {"space": "space", "escape": "Escape", "return": "Return", "tab": "Tab"}.get(key, key)
        keysym = XK.string_to_keysym(name) or XK.string_to_keysym(name.capitalize()) \
            or XK.string_to_keysym(name.upper())
        if not keysym:
            return None
        keycode = self._dpy.keysym_to_keycode(keysym)
        if not keycode:
            return None
        return keycode, mods

    def grab(self, combo, any_modifier: bool = False) -> bool:
        if self._dpy is None:
            return False
        spec = self._combo_to_grab(combo)
        if spec is None:
            return False
        keycode, mods = spec
        key = (keycode, mods, any_modifier)
        with self._lock:
            if key in self._grabs:
                return True
            variants = [X.AnyModifier] if any_modifier else [
                mods, mods | X.LockMask, mods | X.Mod2Mask, mods | X.LockMask | X.Mod2Mask]
            errors = []

            def on_error(err, *_):
                errors.append(err)

            for mask in variants:
                self._root.grab_key(keycode, mask, True, X.GrabModeAsync, X.GrabModeAsync,
                                    onerror=on_error)
            self._dpy.sync()
            if errors:
                log.warning("Сочетание %s уже занято другой программой", combo)
                for mask in variants:
                    self._root.ungrab_key(keycode, mask)
                self._dpy.sync()
                return False
            self._grabs[key] = variants
            return True

    def ungrab(self, combo, any_modifier: bool = False) -> None:
        if self._dpy is None:
            return
        spec = self._combo_to_grab(combo)
        if spec is None:
            return
        keycode, mods = spec
        key = (keycode, mods, any_modifier)
        with self._lock:
            variants = self._grabs.pop(key, None)
            if not variants:
                return
            for mask in variants:
                self._root.ungrab_key(keycode, mask)
            self._dpy.sync()

    def ungrab_all(self) -> None:
        with self._lock:
            for (keycode, _mods, _any), variants in list(self._grabs.items()):
                for mask in variants:
                    self._root.ungrab_key(keycode, mask)
            self._grabs.clear()
            if self._dpy is not None:
                self._dpy.sync()


class HotkeyRecorder:
    """Запись нового сочетания в настройках: собирает максимальный набор нажатых клавиш."""

    def __init__(self):
        self.active = False
        self.state = KeyState()
        self.best: list[str] = []
        self.on_done: Optional[Callable[[list], None]] = None
        self.on_update: Optional[Callable[[list], None]] = None

    def begin(self, on_update, on_done) -> None:
        self.active = True
        self.state = KeyState()
        self.best = []
        self.on_update, self.on_done = on_update, on_done

    def feed(self, kind: str, name: str) -> None:
        if kind == "press":
            self.state.press(name)
            current = self.state.tokens()
            if len(current) >= len(self.best):
                self.best = current
            if self.on_update:
                self.on_update(self.best)
        else:
            self.state.release(name)
            if not self.state.down and self.best:
                self.active = False
                if self.on_done:
                    self.on_done(_generalize(self.best))


def _generalize(tokens: list[str]) -> list[str]:
    """Левый/правый модификатор → общий, если в сочетании есть другие клавиши."""
    if len(tokens) == 1:
        return tokens
    out = []
    for token in tokens:
        if token[:1] in "lr" and token[1:] in MODIFIER_GENERIC:
            out.append(token[1:])
        else:
            out.append(token)
    # Модификаторы — первыми, в привычном порядке.
    order = {"ctrl": 0, "super": 1, "alt": 2, "shift": 3}
    return sorted(out, key=lambda t: (order.get(t, 10), t))


# --------------------------------------------------------------- выделение
NAVIGATION_KEYS = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}


class SelectionTracker:
    """Есть ли сейчас выделенный текст (для Edit Mode).

    В X11 выделение публикуется как PRIMARY. XFixes сообщает о каждой смене
    владельца, а XRecord — о кликах и нажатиях. Выделение считается живым,
    если оно появилось позже последнего «снимающего» действия (клик мышью,
    ввод символа, Enter…). Shift+стрелки и Ctrl+A сами обновляют PRIMARY.
    """

    def __init__(self):
        self.selected_at = 0.0
        self.cleared_at = 0.0
        self._ok = False
        if X is None:
            return
        try:
            self._dpy = display.Display()
            if not self._dpy.has_extension("XFIXES"):
                return
            self._dpy.xfixes_query_version()
            primary = self._dpy.intern_atom("PRIMARY")
            self._dpy.xfixes_select_selection_input(
                self._dpy.screen().root, primary,
                xfixes.XFixesSetSelectionOwnerNotifyMask
                | xfixes.XFixesSelectionWindowDestroyNotifyMask
                | xfixes.XFixesSelectionClientCloseNotifyMask)
            self._dpy.flush()
            self._ok = True
            threading.Thread(target=self._loop, name="xfixes", daemon=True).start()
        except Exception:  # noqa: BLE001
            log.exception("XFixes недоступен — Edit Mode будет выключен")

    @property
    def available(self) -> bool:
        return self._ok

    def _loop(self) -> None:
        while True:
            try:
                event = self._dpy.next_event()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            # python-xlib регистрирует копию класса события, поэтому isinstance не годится.
            kind = type(event).__name__
            if kind == "SetSelectionOwnerNotify":
                if event.owner:
                    self.selected_at = time.monotonic()
                else:
                    self.cleared_at = time.monotonic()
            elif kind in ("SelectionWindowDestroyNotify", "SelectionClientCloseNotify"):
                self.cleared_at = time.monotonic()

    def note_input(self, kind: str, name: str, state: "KeyState") -> None:
        if kind != "press":
            return
        if name == "mouse1" or name in ("mouse2", "mouse3"):
            self.cleared_at = time.monotonic()
            return
        if name in MODIFIER_TOKENS:
            return
        if name in NAVIGATION_KEYS and not state.contains(["shift"]):
            self.cleared_at = time.monotonic()
            return
        if name in NAVIGATION_KEYS:
            return
        if state.contains(["ctrl"]) and name.lower() in ("c", "a", "insert", "x"):
            return
        self.cleared_at = time.monotonic()

    def is_live(self, max_age_s: float = 900.0) -> bool:
        now = time.monotonic()
        return self._ok and self.selected_at > self.cleared_at and now - self.selected_at < max_age_s


def read_x_selection(selection: str = "PRIMARY", timeout: float = 0.4) -> str:
    """Прочитать текст выделения (PRIMARY) или буфера (CLIPBOARD) без участия Qt."""
    if X is None:
        return ""
    dpy = display.Display()
    try:
        win = dpy.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        sel = dpy.intern_atom(selection)
        utf8 = dpy.intern_atom("UTF8_STRING")
        prop = dpy.intern_atom("AQUA_LINUX_SEL")
        win.convert_selection(sel, utf8, prop, X.CurrentTime)
        dpy.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while dpy.pending_events():
                event = dpy.next_event()
                if event.type == X.SelectionNotify:
                    if event.property == X.NONE:
                        return ""
                    data = win.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
                    if data is None:
                        return ""
                    value = data.value
                    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            time.sleep(0.004)
        return ""
    except Exception:  # noqa: BLE001
        log.debug("Не удалось прочитать выделение", exc_info=True)
        return ""
    finally:
        dpy.close()
