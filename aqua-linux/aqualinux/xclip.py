"""Снимок буфера обмена X11 в фоновом потоке, с тайм-аутом.

Перед вставкой Aqua запоминает буфер, чтобы потом вернуть его. Читать его через Qt в главном
потоке нельзя: если программа-владелец буфера зависла, Qt ждёт ответа до 5 с на каждый формат —
и облачко замирает. Здесь — своё соединение с X, общий срок и только нужные форматы
(текст, HTML, файлы, картинка), включая большие (протокол INCR).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    from Xlib import X, display
except ImportError:  # pragma: no cover
    X = None

# Что сохраняем (X-цель → MIME для QMimeData). Текст — в одном виде, остальное Qt достроит сам.
WANTED = (
    ("text/plain;charset=utf-8", "text/plain;charset=utf-8"),
    ("UTF8_STRING", "text/plain;charset=utf-8"),
    ("text/html", "text/html"),
    ("text/uri-list", "text/uri-list"),
    ("x-special/gnome-copied-files", "x-special/gnome-copied-files"),
    ("image/png", "image/png"),
)
LIMIT = 24 << 20      # больше — не сохраняем (не держим в памяти огромные картинки)


def snapshot(selection: str = "CLIPBOARD", timeout: float = 0.6) -> Optional[dict]:
    """{MIME: bytes}; {} — буфер пуст; None — владелец не ответил вовремя (возвращать нечего)."""
    if X is None:
        return None
    deadline = time.monotonic() + timeout
    try:
        dpy = display.Display()
    except Exception:  # noqa: BLE001
        return None
    try:
        sel = dpy.intern_atom(selection)
        if dpy.get_selection_owner(sel) == X.NONE:
            return {}
        win = dpy.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent,
                                              event_mask=X.PropertyChangeMask)
        prop = dpy.intern_atom("AQUA_LINUX_CLIP")
        targets = _convert(dpy, win, sel, dpy.intern_atom("TARGETS"), prop, deadline)
        if targets is None:
            return None
        names = set()
        atoms = targets[1]
        for atom in (atoms if not isinstance(atoms, (bytes, str)) else []):     # array('I') атомов
            try:
                names.add(dpy.get_atom_name(int(atom)))
            except Exception:  # noqa: BLE001
                pass
        out: dict = {}
        for target, mime in WANTED:
            if target not in names or mime in out:
                continue
            got = _convert(dpy, win, sel, dpy.intern_atom(target), prop, deadline)
            if got is None:
                return None if not out else out
            data = got[1]
            if isinstance(data, str):
                data = data.encode("utf-8")
            if isinstance(data, (bytes, bytearray)) and len(data) <= LIMIT:
                out[mime] = bytes(data)
        return out
    except Exception:  # noqa: BLE001
        log.debug("Снимок буфера обмена не удался", exc_info=True)
        return None
    finally:
        try:
            dpy.close()
        except Exception:  # noqa: BLE001
            pass


def _wait(dpy, deadline: float, match) -> Optional[object]:
    while time.monotonic() < deadline:
        while dpy.pending_events():
            event = dpy.next_event()
            if match(event):
                return event
        time.sleep(0.003)
    return None


def _convert(dpy, win, sel, target, prop, deadline: float):
    """Запросить у владельца буфера данные в формате target. (тип, данные) или None."""
    win.convert_selection(sel, target, prop, X.CurrentTime)
    dpy.flush()
    event = _wait(dpy, deadline, lambda e: e.type == X.SelectionNotify and e.requestor.id == win.id)
    if event is None or event.property == X.NONE:
        return None
    reply = win.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
    if reply is None:
        return None
    incr = dpy.intern_atom("INCR")
    if reply.property_type != incr:
        win.delete_property(prop)
        dpy.flush()
        return reply.property_type, reply.value
    # Большие данные приходят частями (INCR): удаляем свойство — владелец кладёт следующую часть.
    chunks = []
    size = 0
    win.delete_property(prop)
    dpy.flush()
    kind = None
    while True:
        event = _wait(dpy, deadline, lambda e: e.type == X.PropertyNotify and e.window.id == win.id
                      and e.atom == prop and e.state == X.PropertyNewValue)
        if event is None:
            return None
        part = win.get_full_property(prop, X.AnyPropertyType, sizehint=1 << 20)
        win.delete_property(prop)
        dpy.flush()
        if part is None:
            return None
        value = part.value
        if isinstance(value, str):
            value = value.encode("utf-8")
        if not value:
            return kind, b"".join(chunks)
        kind = part.property_type
        chunks.append(bytes(value))
        size += len(value)
        if size > LIMIT:
            return None
