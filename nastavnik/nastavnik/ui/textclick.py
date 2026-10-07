"""Щелчок по тексту не должен ничего выделять.

Qt начинает выделять текст мышью с первого пикселя сдвига: если рука чуть дрогнула во время щелчка
(на тачпаде — почти всегда), выделяется одна буква. В Linux выделение сразу становится PRIMARY —
и программы, которые следят за выделением (например, Aqua), думают, что вы что-то выделили.
Проверено на X11: сдвиг на 2–3 px при щелчке по тексту Claude, своему облачку или полю ввода —
и в PRIMARY уходит «и», «р», «м».

Поэтому выделение мышью начинается, только когда указатель ушёл дальше порога перетаскивания
системы (startDragDistance, обычно 10 px) — как перетаскивание в файловом менеджере. Обычное
выделение протяжкой и двойной щелчок по слову работают как раньше.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit, QTextEdit

_installed: dict[int, "ClickJitterFilter"] = {}


def is_text(obj) -> bool:
    """Виджет, в котором мышь выделяет текст."""
    if isinstance(obj, QLabel):
        return bool(obj.textInteractionFlags() & Qt.TextSelectableByMouse)
    if isinstance(obj, QLineEdit):
        return True
    parent = obj.parent() if isinstance(obj, QObject) else None
    return isinstance(parent, (QPlainTextEdit, QTextEdit)) and obj is parent.viewport()


class ClickJitterFilter(QObject):
    def __init__(self, threshold: int | None = None):
        super().__init__()
        self.threshold = threshold
        self._target = None
        self._press = None
        self._dragging = False

    def limit(self) -> int:
        return self.threshold if self.threshold is not None else max(6, QApplication.startDragDistance())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton and is_text(obj):
                self._target, self._press, self._dragging = obj, event.globalPosition(), False
            else:
                self._target = None
        elif kind == QEvent.MouseMove and obj is self._target and not self._dragging:
            if event.buttons() & Qt.LeftButton:
                d = event.globalPosition() - self._press
                if abs(d.x()) + abs(d.y()) < self.limit():
                    return True                       # дрожание руки при щелчке — не выделение
                self._dragging = True
        elif kind == QEvent.MouseButtonRelease and obj is self._target:
            self._target = None
        return False


def install(app: QApplication) -> ClickJitterFilter:
    """Один фильтр на приложение (повторный вызов ничего не добавляет)."""
    f = _installed.get(id(app))
    if f is None:
        f = ClickJitterFilter()
        app.installEventFilter(f)
        _installed[id(app)] = f
    return f
