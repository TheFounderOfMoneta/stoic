"""Чат с Claude: текст Claude — как статья в Читалке «Сводки», ваши сообщения — облачками справа.

- Ответ появляется потоком, по мере генерации, но перерисовывается не чаще 30 раз в секунду
  и только последнее сообщение — окно не замирает даже на длинных ответах.
- Свой маленький Markdown (абзацы, жирный/курсив, списки, заголовки, код): полный контроль над
  типографикой. Схемы Claude в блоках ```mermaid рисуются картинкой (по щелчку — на доску, поправить
  от руки); прочий код — моноширинным шрифтом на карточке.
- Схема, отправленная с доски, — облачко с миниатюрой: видно, что именно ушло Claude.
- Если вы прокрутили вверх, чат не дёргает вас вниз при каждом новом слове.
"""
from __future__ import annotations

import html
import re

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import sketch
from .look import LINE_HEIGHT, READER_W

_FENCE = re.compile(r"^\s*```")
_UL = re.compile(r"^\s*[-*•]\s+(.*)$")
_OL = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_H = re.compile(r"^\s*(#{1,4})\s+(.*)$")


def inline(text: str) -> str:
    """Экранирование и строчная разметка: **жирный**, *курсив*, `код`, [ссылка](адрес)."""
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r'<span style="font-family:monospace;">\1</span>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_blocks(text: str) -> list[tuple[str, object]]:
    """Разбить Markdown на блоки: p, h, quote (html), code (как есть), ul/ol (список html)."""
    blocks: list[tuple[str, object]] = []
    lines = (text or "").replace("\r", "").split("\n")
    i = 0
    para: list[str] = []

    def flush():
        if para:
            blocks.append(("p", "<br>".join(inline(x.strip()) for x in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            flush()
            lang = line.strip()[3:].strip().lower()
            code = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            closed = i < len(lines)
            body = "\n".join(code).rstrip("\n")
            # схема — когда блок дописан: пока Claude её печатает, это просто код
            blocks.append(("mermaid" if lang == "mermaid" and closed else "code", body))
            i += 1
            continue
        m = _H.match(line)
        if m:
            flush()
            blocks.append(("h", inline(m.group(2).strip().strip("#").strip())))
            i += 1
            continue
        if _UL.match(line) or _OL.match(line):
            flush()
            ordered = bool(_OL.match(line)) and not _UL.match(line)
            items: list[str] = []
            while i < len(lines) and (_UL.match(lines[i]) or _OL.match(lines[i]) or
                                      (items and lines[i].startswith(("  ", "\t")) and lines[i].strip())):
                um, om = _UL.match(lines[i]), _OL.match(lines[i])
                if um:
                    items.append(inline(um.group(1)))
                elif om:
                    items.append(inline(om.group(2)))
                else:
                    items[-1] += " " + inline(lines[i].strip())
                i += 1
            blocks.append(("ol" if ordered else "ul", items))
            continue
        if line.strip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(("quote", "<br>".join(inline(q) for q in quote)))
            continue
        if not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def _wrap(html_text: str) -> str:
    return f'<div style="line-height:{LINE_HEIGHT}%;">{html_text}</div>'


def block_html(kind: str, content) -> str:
    if kind == "ul":
        return _wrap("<br>".join(f"•&nbsp;&nbsp;{x}" for x in content))
    if kind == "ol":
        return _wrap("<br>".join(f"{k}.&nbsp;&nbsp;{x}" for k, x in enumerate(content, start=1)))
    if kind == "quote":
        return _wrap(f"<i>{content}</i>")
    return _wrap(str(content))


class SchemaBlock(QWidget):
    """Схема Claude из блока mermaid: картинка, «Открыть на доске» и исходный текст по запросу."""

    def __init__(self, code: str, board: sketch.Board, on_open=None):
        super().__init__()
        from .board import SchemaView
        self.code = code
        self.board = board
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.view = SchemaView(board, max_h=460, clickable=on_open is not None)
        if on_open:
            self.view.clicked.connect(lambda: on_open(self.board.copy()))
        lay.addWidget(self.view)
        row = QHBoxLayout()
        row.setSpacing(4)
        if on_open:
            b = QPushButton("Открыть на доске")
            b.setObjectName("Link")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Схема окажется на доске: дорисуйте, поправьте и отправьте обратно")
            b.clicked.connect(lambda: on_open(self.board.copy()))
            row.addWidget(b)
        self.src_btn = QPushButton("Текст схемы")
        self.src_btn.setObjectName("Link")
        self.src_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.src_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.src = QLabel(code)
        self.src.setObjectName("Code")
        self.src.setTextFormat(Qt.PlainText)
        self.src.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.src.hide()
        lay.addWidget(self.src)
        self.src_btn.clicked.connect(lambda: self.src.setVisible(not self.src.isVisible()))


class AssistantMessage(QWidget):
    """Сообщение Claude: блоки текста, переиспользуемые при потоковом обновлении."""

    def __init__(self, text: str = "", on_schema=None):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.labels: list[tuple[str, QWidget]] = []
        self.text = ""
        self.on_schema = on_schema
        self._parsed: dict[str, sketch.Board | None] = {}
        if text:
            self.set_text(text)

    def _schema(self, code: str) -> sketch.Board | None:
        if code not in self._parsed:
            from .board import schema_from_mermaid
            self._parsed[code] = schema_from_mermaid(code)
        return self._parsed[code]

    def _name(self, kind: str, content) -> str:
        if kind == "mermaid":
            return "Schema" if self._schema(str(content)) is not None else "Code"
        return {"code": "Code", "h": "BodyH"}.get(kind, "Body")

    def _make(self, kind: str, content) -> tuple[str, QWidget]:
        name = self._name(kind, content)
        if name == "Schema":
            return name, SchemaBlock(str(content), self._schema(str(content)), self.on_schema)
        if name == "Code":
            return name, CodeView()
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        label.setOpenExternalLinks(True)
        label.setObjectName(name)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return name, label

    def set_text(self, text: str) -> None:
        if text == self.text:
            return
        self.text = text
        blocks = md_blocks(text)
        for k, (kind, content) in enumerate(blocks):
            name = self._name(kind, content)
            reuse = k < len(self.labels) and self.labels[k][0] == name
            if reuse and name == "Schema" and self.labels[k][1].code != content:
                reuse = False
            if not reuse:
                name, widget = self._make(kind, content)
                if k < len(self.labels):
                    old = self.labels[k][1]
                    self.lay.replaceWidget(old, widget)
                    old.deleteLater()
                    self.labels[k] = (name, widget)
                else:
                    self.lay.addWidget(widget)
                    self.labels.append((name, widget))
            name, label = self.labels[k]
            if name == "Schema":
                continue
            if kind in ("code", "mermaid"):
                if label.text() != content:
                    label.setText(str(content))
            else:
                label.setTextFormat(Qt.RichText)
                new = block_html(kind, content)
                if label.text() != new:
                    label.setText(new)
        while len(self.labels) > len(blocks):
            _, old = self.labels.pop()
            self.lay.removeWidget(old)
            old.deleteLater()

    def schemas(self) -> list[SchemaBlock]:
        return [w for name, w in self.labels if name == "Schema"]


class UserMessage(QWidget):
    def __init__(self, text: str, note: str = "", image=None, caption: str = "", on_image=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(80, 0, 0, 0)
        self.row = row
        row.addStretch(1)
        bubble = QFrame()
        bubble.setObjectName("UserBubble")
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(14, 9, 14, 9)
        bl.setSpacing(5)
        self.image = None
        self._image = image
        if image is not None:                       # схема с доски — миниатюрой
            self.image = QLabel()
            self.image.setObjectName("BoardThumb")
            if on_image:
                self.image.setCursor(Qt.PointingHandCursor)
                self.image.setToolTip("Открыть доску")
                self.image.mousePressEvent = lambda _e: on_image()
            bl.addWidget(self.image, 0, Qt.AlignRight)
            if caption:
                cap = QLabel(caption)
                cap.setObjectName("Meta")
                cap.setAlignment(Qt.AlignRight)
                cap.setWordWrap(True)
                bl.addWidget(cap)
        self.label = QLabel(text)
        self.label.setObjectName("UserText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # перенос строк у QLabel не знает желаемой ширины — задаём её по самой длинной строке (см. fit)
        self._want = max((self.label.fontMetrics().horizontalAdvance(line) for line in text.split("\n")), default=0) + 4
        bl.addWidget(self.label)
        self.label.setVisible(bool(text))
        if note:
            n = QLabel(note)
            n.setObjectName("Meta")
            n.setAlignment(Qt.AlignRight)
            bl.addWidget(n)
        row.addWidget(bubble)
        self.fit(READER_W)

    def fit(self, avail: int) -> None:
        """Подогнать под ширину колонки: рядом с доской чат узкий, и облачко не должно его распирать."""
        indent = 80 if avail >= 520 else 24
        self.row.setContentsMargins(indent, 0, 0, 0)
        inner = max(140, avail - indent - 30)
        self.label.setMaximumWidth(min(500, inner))
        self.label.setMinimumWidth(min(500, inner, self._want))
        if self._image is not None:
            w = min(460, inner)
            pix = QPixmap.fromImage(self._image)
            if pix.width() > w or pix.height() > 300:
                pix = pix.scaled(w, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image.setPixmap(pix)


class CodeView(QScrollArea):
    """Код и текстовые схемы — без переноса строк; в узкой колонке прокручиваются вбок."""

    def __init__(self):
        super().__init__()
        self.setObjectName("CodeScroll")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.viewport().setAutoFillBackground(False)
        self.label = QLabel()
        self.label.setObjectName("Code")
        self.label.setTextFormat(Qt.PlainText)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setWidget(self.label)

    def setText(self, text: str) -> None:  # noqa: N802
        self.label.setText(text)
        self.label.adjustSize()
        bar = self.horizontalScrollBar().sizeHint().height() if self.label.sizeHint().width() > self.width() else 0
        self.setFixedHeight(self.label.sizeHint().height() + bar + 2)

    def text(self) -> str:
        return self.label.text()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.setText(self.label.text())


class ChatView(QScrollArea):
    """Лента сообщений по центру окна, колонка 680 px."""

    schema_open = Signal(object)        # Board — схему Claude открыть на доске
    board_open = Signal()               # щёлкнули по своей схеме

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(36, 26, 36, 26)
        self.column = QWidget()
        self.column.setMaximumWidth(READER_W)
        self.col = QVBoxLayout(self.column)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(18)
        self.col.addStretch(1)
        row.addStretch(1)
        row.addWidget(self.column, 100)
        row.addStretch(1)
        self.setWidget(body)
        self.thinking = QLabel("")
        self.thinking.setObjectName("Thinking")
        self.thinking.hide()
        self.col.addWidget(self.thinking)
        self.current: AssistantMessage | None = None
        self._buffer = ""
        self._stick = True
        self._render = QTimer(self)
        self._render.setSingleShot(True)
        self._render.setInterval(33)
        self._render.timeout.connect(self._flush)
        sb = self.verticalScrollBar()
        sb.valueChanged.connect(self._scrolled)
        sb.rangeChanged.connect(self._range_changed)

    # ------------------------------------------------------------- прокрутка
    def _scrolled(self, value: int) -> None:
        sb = self.verticalScrollBar()
        self._stick = value >= sb.maximum() - 40

    def _range_changed(self, _lo: int, hi: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(hi)

    def _insert(self, widget: QWidget) -> QWidget:
        self.col.insertWidget(self.col.count() - 1, widget)       # перед строкой «думает…»
        self._stick = True
        if isinstance(widget, UserMessage):
            widget.fit(self._avail())
        return widget

    def _margin(self) -> int:
        return 36 if self.viewport().width() >= 560 else 16

    def _avail(self) -> int:
        return max(200, min(READER_W, self.viewport().width() - 2 * self._margin()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        m = self._margin()
        self.widget().layout().setContentsMargins(m, 26, m, 26)
        avail = self._avail()
        for m in self.column.findChildren(UserMessage):
            m.fit(avail)

    # ------------------------------------------------------------- сообщения
    def add_user(self, text: str, confidence: int | None = None) -> None:
        note = {1: "наугад", 2: "не уверен", 3: "скорее уверен", 4: "уверен"}.get(confidence or 0, "")
        self._insert(UserMessage(text, note))

    def add_board(self, image, caption: str, comment: str = "", confidence: int | None = None) -> UserMessage:
        note = {1: "наугад", 2: "не уверен", 3: "скорее уверен", 4: "уверен"}.get(confidence or 0, "")
        msg = UserMessage(comment, note, image=image, caption=caption, on_image=self.board_open.emit)
        self._insert(msg)
        return msg

    def add_assistant(self, text: str = "") -> AssistantMessage:
        self.current = AssistantMessage(text, on_schema=self.schema_open.emit)
        self._buffer = text
        self._insert(self.current)
        return self.current

    def stream(self, piece: str) -> None:
        if self.current is None:
            self.add_assistant()
        self._buffer += piece
        if not self._render.isActive():
            self._render.start()

    def _flush(self) -> None:
        if self.current is not None:
            self.current.set_text(self._buffer)

    def end_stream(self, final: str | None = None) -> None:
        self._render.stop()
        if self.current is not None:
            text = final if final else self._buffer
            if text.strip():
                self.current.set_text(text)
            else:
                self.current.hide()
        self.current = None
        self._buffer = ""
        self.set_thinking(None)

    def add_note(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("AppNote")
        lab.setWordWrap(True)
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(lab)
        lay.addStretch(1)
        self._insert(wrap)
        return lab

    def add_widget(self, widget: QWidget) -> QWidget:
        return self._insert(widget)

    def set_thinking(self, text: str | None) -> None:
        if text:
            self.thinking.setText(text)
            self.thinking.show()
        else:
            self.thinking.hide()

    def assistant_texts(self) -> list[str]:
        return [w.text for w in self.column.findChildren(AssistantMessage)]

    def clear(self) -> None:
        self._render.stop()
        self.current = None
        self._buffer = ""
        while self.col.count() > 2:
            item = self.col.takeAt(1)
            w = item.widget()
            if w is not None and w is not self.thinking:
                w.deleteLater()
        self.set_thinking(None)


class ChatInput(QWidget):
    """Поле ввода: Enter — отправить, Shift+Enter — новая строка. Для учёбы — уверенность в ответе."""

    submitted = Signal(str, object)

    def __init__(self, placeholder: str = "Напишите ответ…", confidence: bool = False):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.conf_row = QWidget()
        cr = QHBoxLayout(self.conf_row)
        cr.setContentsMargins(4, 0, 0, 0)
        cr.setSpacing(6)
        lab = QLabel("Уверенность:")
        lab.setObjectName("Meta")
        cr.addWidget(lab)
        self.conf_group = QButtonGroup(self)
        self.conf_group.setExclusive(False)
        self.conf_buttons = []
        for k, text in ((1, "наугад"), (2, "не уверен"), (3, "скорее да"), (4, "уверен")):
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"Клавиши Alt+{k}")
            b.clicked.connect(lambda _=False, kk=k: self._pick(kk))
            self.conf_buttons.append(b)
            cr.addWidget(b)
        cr.addStretch(1)
        self.conf_layout = cr                      # справа — место для кнопок сессии (доска)
        outer.addWidget(self.conf_row)
        self.conf_row.setVisible(confidence)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("ChatInput")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setTabChangesFocus(True)
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._grow)
        self.edit.setFixedHeight(46)
        row.addWidget(self.edit, 1)
        self.send = QPushButton("Отправить")
        self.send.setObjectName("Primary")
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.clicked.connect(self.submit)
        row.addWidget(self.send, 0, Qt.AlignBottom)
        outer.addLayout(row)
        self.confidence: int | None = None

    def _pick(self, k: int) -> None:
        self.confidence = None if self.confidence == k else k
        for i, b in enumerate(self.conf_buttons, start=1):
            b.setChecked(i == self.confidence)

    def _grow(self) -> None:
        doc_h = int(self.edit.document().size().height() * self.edit.fontMetrics().lineSpacing())
        self.edit.setFixedHeight(max(46, min(170, doc_h + 22)))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                self.submit()
                return True
            if event.modifiers() & Qt.AltModifier and Qt.Key_1 <= event.key() <= Qt.Key_4 \
                    and self.conf_row.isVisible():
                self._pick(event.key() - Qt.Key_0)
                return True
        return False

    def take(self) -> tuple[str, int | None]:
        """Забрать текст и уверенность (поле очищается): комментарий к схеме с доски."""
        text = self.edit.toPlainText().strip()
        conf = self.confidence if self.conf_row.isVisible() else None
        self.edit.clear()
        self.confidence = None                          # уверенность — к каждому ответу заново
        for b in self.conf_buttons:
            b.setChecked(False)
        return text, conf

    def submit(self) -> None:
        if not self.edit.toPlainText().strip() or not self.send.isEnabled():
            return
        text, conf = self.take()
        self.submitted.emit(text, conf)

    def set_busy(self, busy: bool) -> None:
        self.send.setEnabled(not busy)
        self.send.setText("Claude отвечает…" if busy else "Отправить")
        if not busy:
            self.edit.setFocus()
