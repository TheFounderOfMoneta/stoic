"""Виджеты в стиле macOS: группы настроек, переключатель, клавиши, уровень, график, живой шар."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..hotkeys import pretty_token

# Палитра задаётся окном при создании (theme.palette()).
PALETTE: dict = {}


def pc(key: str, fallback: str = "#3A8DFF") -> QColor:
    value = PALETTE.get(key, fallback)
    if isinstance(value, QColor):
        return QColor(value)
    if isinstance(value, str) and value.startswith("rgba("):
        r, g, b, a = [int(x) for x in value[5:-1].split(",")]
        return QColor(r, g, b, a)
    return QColor(value)


class Switch(QAbstractButton):
    """Переключатель как в macOS: акцентный, с мягкой анимацией ручки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 24)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def get_pos(self) -> float:
        return self._pos

    def set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, get_pos, set_pos)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 3, 38, 18)
        off, on = pc("switch_off", "#3A404D"), pc("accent")
        t = self._pos
        bg = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                    int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            bg.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 9, 9)
        x = r.left() + 1.5 + (r.width() - 18) * t
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(QRectF(x, r.top() + 2, 15, 15))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, r.top() + 1.5, 15, 15))
        p.end()


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


class Card(QFrame):
    """Скруглённый контейнер."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None, padding: int = 16):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding - 2, padding, padding - 2)
        self.lay.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("CardTitle")
            self.lay.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            self.lay.addWidget(sub)

    def add(self, widget) -> QWidget:
        self.lay.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.lay.addLayout(layout)


class Group(QWidget):
    """Группа настроек как в «Системных настройках» macOS: подпись сверху, строки в карточке."""

    def __init__(self, title: str = "", footer: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(7)
        if title:
            label = QLabel(title.upper())
            label.setObjectName("SectionLabel")
            label.setContentsMargins(12, 0, 0, 0)
            outer.addWidget(label)
        self.card = QFrame()
        self.card.setObjectName("Card")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(14, 4, 14, 4)
        self.rows.setSpacing(0)
        outer.addWidget(self.card)
        if footer:
            foot = QLabel(footer)
            foot.setObjectName("Muted")
            foot.setWordWrap(True)
            foot.setContentsMargins(12, 0, 12, 0)
            outer.addWidget(foot)
        self._count = 0

    def add_row(self, title: str, description: str = "", control: QWidget | None = None) -> "SettingRow":
        row = SettingRow(title, description, control)
        self.add_widget(row)
        return row

    def add_widget(self, widget: QWidget) -> QWidget:
        if self._count:
            self.rows.addWidget(divider())
        self.rows.addWidget(widget)
        self._count += 1
        return widget


class SettingRow(QWidget):
    def __init__(self, title: str, description: str, control: QWidget | None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(18)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        t = QLabel(title)
        t.setWordWrap(True)
        texts.addWidget(t)
        if description:
            d = QLabel(description)
            d.setObjectName("Muted")
            d.setWordWrap(True)
            texts.addWidget(d)
        lay.addLayout(texts, 1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)


def keycap(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Keycap")
    label.setAlignment(Qt.AlignCenter)
    return label


def keycaps_row(combo, extra: str = "") -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for i, token in enumerate(combo or []):
        if i:
            plus = QLabel("+")
            plus.setObjectName("Muted")
            lay.addWidget(plus)
        lay.addWidget(keycap(pretty_token(token)))
    if extra:
        e = QLabel(extra)
        e.setObjectName("Soft")
        lay.addSpacing(8)
        lay.addWidget(e)
    lay.addStretch(1)
    return w


class StatTile(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)
        self.value = QLabel("—")
        self.value.setObjectName("StatValue")
        self.caption = QLabel(label)
        self.caption.setObjectName("StatLabel")
        self.caption.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set(self, value: str) -> None:
        self.value.setText(value)


class LevelMeter(QWidget):
    """Индикатор уровня микрофона: ряд скруглённых делений."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setMinimumHeight(12)
        self.setMinimumWidth(180)
        self._decay = QTimer(self)
        self._decay.timeout.connect(self._fade)
        self._decay.start(40)

    def set_level(self, level: float) -> None:
        self.level = max(self.level * 0.7, level)
        self.update()

    def _fade(self) -> None:
        if self.level > 0.001:
            self.level *= 0.86
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = 30
        gap = 3
        w = (self.width() - gap * (n - 1)) / n
        lit = int(round(self.level * n))
        h = min(8, self.height())
        y = (self.height() - h) / 2
        for i in range(n):
            if i < lit:
                color = pc("accent") if i < n * 0.8 else QColor("#FFB547") if i < n * 0.92 else QColor("#FF5F57")
            else:
                color = pc("switch_off", "#3A404D")
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(i * (w + gap), y, w, h), 2, 2)
        p.end()


class OrbWidget(QWidget):
    """Живой шар Aqua на главной: «дышит», переливается, реагирует на микрофон."""

    def __init__(self, size: int = 64, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.level = 0.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    def set_level(self, level: float) -> None:
        self.level = max(self.level * 0.8, level)

    def showEvent(self, e) -> None:  # noqa: N802
        self._timer.start(33)
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = time.monotonic() - self._t0
        self.level *= 0.9
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        c = QPointF(s / 2, s / 2)
        r = s * 0.27 * (1 + 0.03 * math.sin(t * 1.8) + 0.15 * self.level)
        accent = pc("accent")
        glow = QRadialGradient(c, s / 2)
        g0 = QColor(accent)
        g0.setAlphaF(min(1.0, 0.35 + 0.1 * math.sin(t * 1.8) + 0.4 * self.level))
        glow.setColorAt(0.0, g0)
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, s / 2, s / 2)
        body = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 1.45)
        body.setColorAt(0.0, QColor("#F2FBFF"))
        body.setColorAt(0.28, QColor("#9FE0FF"))
        body.setColorAt(0.62, accent)
        body.setColorAt(1.0, QColor("#1238B8"))
        p.setBrush(QBrush(body))
        p.drawEllipse(c, r, r)
        clip = QPainterPath()
        clip.addEllipse(c, r, r)
        p.setClipPath(clip)
        for i, (col, alpha, scale) in enumerate(((QColor(108, 242, 255), 140, .62), (QColor(61, 91, 255), 115, .7),
                                                 (QColor(255, 255, 255), 70, .38))):
            ang = t * (0.9 + 0.35 * i) + i * 2.1
            o = QPointF(c.x() + math.cos(ang) * r * .42, c.y() + math.sin(ang * 1.3) * r * .36)
            g = QRadialGradient(o, r * scale)
            c0 = QColor(col)
            c0.setAlpha(alpha)
            g.setColorAt(0, c0)
            c0.setAlpha(0)
            g.setColorAt(1, c0)
            p.setBrush(QBrush(g))
            p.drawEllipse(o, r * scale, r * scale)
        p.setClipping(False)
        p.setBrush(QColor(255, 255, 255, 180))
        p.drawEllipse(QPointF(c.x() - r * .38, c.y() - r * .48), r * .3, r * .18)
        p.end()


class HotkeyEditor(QWidget):
    """Сочетания для действия (как в Aqua: несколько штук, до 5 клавиш, запись по ✎)."""

    changed = Signal()

    def __init__(self, app, key: str, allow_empty: bool = True, max_bindings: int = 3, parent=None):
        super().__init__(parent)
        self.app = app
        self.key = key
        self.allow_empty = allow_empty
        self.max_bindings = max_bindings
        self._recording_index = None
        self._live_combo = None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        app.hotkey_captured.connect(self._captured)
        self.rebuild()

    def combos(self) -> list:
        return [c for c in (self.app.settings.get(f"hotkeys.{self.key}") or []) if c]

    def rebuild(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        combos = self.combos()
        for index, combo in enumerate(combos):
            self.lay.addWidget(self._row(index, combo))
        if self._recording_index == len(combos):
            self.lay.addWidget(self._row(len(combos), None))
        if len(combos) < self.max_bindings and self._recording_index is None:
            add = QPushButton("+ Добавить" if combos else "Назначить")
            add.setObjectName("Link")
            add.setCursor(Qt.PointingHandCursor)
            add.clicked.connect(lambda: self._record(len(self.combos())))
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch(1)
            row.addWidget(add)
            self.lay.addWidget(holder)

    def _row(self, index: int, combo) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addStretch(1)
        if self._recording_index == index:
            if self._live_combo:
                row.addWidget(keycaps_row(self._live_combo))
            else:
                hint = QLabel("Нажмите клавиши…  Esc — отмена")
                hint.setObjectName("Badge")
                row.addWidget(hint)
        else:
            row.addWidget(keycaps_row(combo))
            edit = QPushButton("✎")
            edit.setObjectName("Icon")
            edit.setToolTip("Изменить")
            edit.setCursor(Qt.PointingHandCursor)
            edit.clicked.connect(lambda: self._record(index))
            row.addWidget(edit)
            if self.allow_empty or len(self.combos()) > 1:
                remove = QPushButton("✕")
                remove.setObjectName("Icon")
                remove.setToolTip("Удалить")
                remove.setCursor(Qt.PointingHandCursor)
                remove.clicked.connect(lambda: self._remove(index))
                row.addWidget(remove)
        return holder

    def _record(self, index: int) -> None:
        self._recording_index = index
        self._live_combo = None
        self.app.begin_hotkey_capture()
        self.rebuild()

    def _remove(self, index: int) -> None:
        combos = self.combos()
        if 0 <= index < len(combos):
            combos.pop(index)
            self.app.settings.set(f"hotkeys.{self.key}", combos)
            self.rebuild()
            self.changed.emit()

    def _captured(self, combo: list, final: bool) -> None:
        if self._recording_index is None:
            return
        if not final:
            self._live_combo = combo[:5]
            self.rebuild()
            return
        index = self._recording_index
        self._recording_index = None
        self._live_combo = None
        self.app.end_hotkey_capture()
        if combo == ["escape"] and self.key != "cancel":
            self.rebuild()
            return
        combos = self.combos()
        combo = combo[:5]
        if index < len(combos):
            combos[index] = combo
        else:
            combos.append(combo)
        self.app.settings.set(f"hotkeys.{self.key}", combos)
        self.rebuild()
        self.changed.emit()


# ---------------------------------------------------------------- привязки к настройкам
def bind_switch(settings, key: str, on_change=None) -> Switch:
    sw = Switch()
    sw.setChecked(bool(settings.get(key)))

    def changed(value):
        settings.set(key, bool(value))
        if on_change:
            on_change(bool(value))

    sw.toggled.connect(changed)
    return sw


def bind_combo(settings, key: str, options: list, on_change=None) -> QComboBox:
    """options: [(value, label)]"""
    box = QComboBox()
    box.setMinimumWidth(240)
    box.setCursor(Qt.PointingHandCursor)
    current = settings.get(key)
    for value, label in options:
        box.addItem(label, value)
    index = next((i for i, (v, _) in enumerate(options) if v == current), 0)
    box.setCurrentIndex(index)

    def changed(i):
        settings.set(key, box.itemData(i))
        if on_change:
            on_change(box.itemData(i))

    box.currentIndexChanged.connect(changed)
    return box


def bind_spin(settings, key: str, lo: int, hi: int, suffix: str = "", step: int = 1) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setSingleStep(step)
    spin.setSuffix(suffix)
    spin.setValue(int(settings.get(key) or 0))
    spin.valueChanged.connect(lambda v: settings.set(key, int(v)))
    spin.setButtonSymbols(QAbstractSpinBox.NoButtons)   # колесо мыши и ввод с клавиатуры
    spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    spin.setFixedWidth(110)
    return spin


def bind_line(settings, key: str, placeholder: str = "", password: bool = False) -> QLineEdit:
    line = QLineEdit(str(settings.get(key) or ""))
    line.setPlaceholderText(placeholder)
    if password:
        line.setEchoMode(QLineEdit.Password)
    line.editingFinished.connect(lambda: settings.set(key, line.text().strip()))
    line.setMinimumWidth(280)
    return line


# ---------------------------------------------------------------- простота и обучение
class _Chevron(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.angle = 0.0

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(8, 8)
        p.rotate(self.angle)
        from PySide6.QtGui import QPen
        p.setPen(QPen(pc("muted", "#828C9E"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(-2, -4.5), QPointF(2.5, 0))
        p.drawLine(QPointF(2.5, 0), QPointF(-2, 4.5))
        p.end()


class Section(QFrame):
    """Сворачиваемая карточка «Дополнительно» (как строка «Системных настроек» macOS со стрелкой)."""

    def __init__(self, title: str, subtitle: str = "", expanded: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QPushButton()
        self.header.setObjectName("SectionHeader")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(16, 12, 16, 12)
        hl.setSpacing(4)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        texts.addWidget(t)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            texts.addWidget(sub)
        hl.addLayout(texts, 1)
        self.chevron = _Chevron()
        hl.addWidget(self.chevron, 0, Qt.AlignVCenter)
        self.header.setMinimumHeight(58 if subtitle else 44)
        for child in self.header.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.header.toggled.connect(self._toggle)
        outer.addWidget(self.header)
        self.body = QWidget()
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(16, 0, 16, 6)
        self.rows.setSpacing(0)
        outer.addWidget(self.body)
        self._count = 0
        self.header.setChecked(expanded)
        self._toggle(expanded)

    def _toggle(self, on: bool) -> None:
        self.body.setVisible(on)
        self.chevron.angle = 90.0 if on else 0.0
        self.chevron.update()

    def add_row(self, title: str, description: str = "", control: QWidget | None = None):
        return self.add_widget(SettingRow(title, description, control))

    def add_widget(self, widget: QWidget) -> QWidget:
        self.rows.addWidget(divider())
        self.rows.addWidget(widget)
        self._count += 1
        return widget


class Segmented(QWidget):
    """Переключатель вкладок как в macOS (две-три кнопки в капсуле)."""

    changed = Signal(int)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.buttons = []
        for i, label in enumerate(labels):
            b = QPushButton(label)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.changed.emit(i))
            lay.addWidget(b)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class HelpButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("?", parent)
        self.setObjectName("Help")
        self.setFixedSize(28, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Что можно сделать на этой странице")


class Coach(QWidget):
    """Обучающие подсказки поверх страницы: затемнение, подсвеченный элемент и карточка с текстом.

    steps: [(widget | None, заголовок, текст)]
    """

    finished = Signal()

    def __init__(self, host: QWidget, steps: list, parent=None):
        super().__init__(host)
        self.host = host
        self.steps = steps
        self.index = 0
        self.progress = 0.0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.card = QFrame(self)
        self.card.setObjectName("CoachCard")
        self.card.setFixedWidth(370)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(20, 18, 20, 16)
        cl.setSpacing(8)
        self.counter = QLabel()
        self.counter.setObjectName("SectionLabel")
        self.title = QLabel()
        self.title.setObjectName("CoachTitle")
        self.title.setWordWrap(True)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setObjectName("Soft")
        cl.addWidget(self.counter)
        cl.addWidget(self.title)
        cl.addWidget(self.text)
        cl.addSpacing(6)
        row = QHBoxLayout()
        self.skip = QPushButton("Пропустить")
        self.skip.setObjectName("Link")
        self.skip.setCursor(Qt.PointingHandCursor)
        self.skip.clicked.connect(self.close_coach)
        self.back = QPushButton("Назад")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(lambda: self.go(self.index - 1))
        self.next = QPushButton("Далее")
        self.next.setObjectName("Primary")
        self.next.setCursor(Qt.PointingHandCursor)
        self.next.clicked.connect(lambda: self.go(self.index + 1))
        row.addWidget(self.skip)
        row.addStretch(1)
        row.addWidget(self.back)
        row.addWidget(self.next)
        cl.addLayout(row)
        host.installEventFilter(self)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._fade)
        self.setGeometry(host.rect())
        self.show()
        self.raise_()
        self.go(0)

    def _fade(self) -> None:
        self.progress = min(1.0, self.progress + 0.12)
        self.update()
        if self.progress >= 1.0:
            self._anim.stop()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.host and event.type() == event.Type.Resize:
            self.setGeometry(self.host.rect())
            self._place()
        return False

    def go(self, index: int) -> None:
        if index >= len(self.steps):
            self.close_coach()
            return
        self.index = max(0, index)
        target, title, text = self.steps[self.index]
        self.counter.setText(f"ПОДСКАЗКА {self.index + 1} ИЗ {len(self.steps)}")
        self.title.setText(title)
        self.text.setText(text)
        self.back.setVisible(self.index > 0)
        self.next.setText("Понятно" if self.index == len(self.steps) - 1 else "Далее")
        self.skip.setVisible(self.index < len(self.steps) - 1)
        if target is not None:
            scroll = target.parent()
            from PySide6.QtWidgets import QScrollArea
            while scroll is not None and not isinstance(scroll, QScrollArea):
                scroll = scroll.parent()
            if scroll is not None:
                scroll.ensureWidgetVisible(target, 40, 120)
        self.progress = 0.0
        self._anim.start(16)
        QTimer.singleShot(0, self._place)

    def _target_rect(self) -> QRectF | None:
        target = self.steps[self.index][0]
        if target is None or not target.isVisible():
            return None
        top_left = target.mapTo(self.host, target.rect().topLeft())
        return QRectF(top_left.x(), top_left.y(), target.width(), target.height()).adjusted(-8, -8, 8, 8)

    def _place(self) -> None:
        self.card.adjustSize()
        rect = self._target_rect()
        w, h = self.card.width(), self.card.sizeHint().height()
        if rect is None:
            x, y = (self.width() - w) / 2, (self.height() - h) / 2
        else:
            x = min(max(16, rect.center().x() - w / 2), self.width() - w - 16)
            if rect.bottom() + 14 + h < self.height() - 10:
                y = rect.bottom() + 14
            elif rect.top() - 14 - h > 10:
                y = rect.top() - 14 - h
            else:
                y = (self.height() - h) / 2
        self.card.move(int(x), int(y))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        rect = self._target_rect()
        if rect is not None:
            hole = QPainterPath()
            hole.addRoundedRect(rect, 14, 14)
            path = path.subtracted(hole)
        p.fillPath(path, QColor(4, 7, 13, int(165 * self.progress)))
        if rect is not None:
            ring = pc("accent")
            ring.setAlpha(int(230 * self.progress))
            from PySide6.QtGui import QPen
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 14, 14)
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()  # клики мимо карточки не проходят на страницу

    def close_coach(self) -> None:
        self.host.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        self.finished.emit()
