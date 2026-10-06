"""Список истории без тормозов: рисуются только видимые строки (QListView + свой делегат),
следующие записи подгружаются при прокрутке. Тысячи записей открываются мгновенно.

Строки двух видов: заголовок дня («СЕГОДНЯ», «ВЧЕРА», «5 ОКТЯБРЯ, ПОНЕДЕЛЬНИК») и карточка
записи. Кнопки карточки («Копировать», «▶ Слушать», «Удалить») появляются при наведении.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Callable, Optional

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListView, QMenu, QStyle, QStyledItemDelegate

from . import widgets as W

PAD_X, PAD_Y = 16, 10        # поля карточки
GAP = 10                     # между карточками
META_H = 24                  # строка со временем и кнопками
LONG = 700                   # длиннее — показываем свёрнутым


class HistoryModel(QAbstractListModel):
    """Записи истории постранично: fetchMore() вызывает сам список при прокрутке к концу."""

    def __init__(self, history, page_size: int = 30, day_title: Callable = str):
        super().__init__()
        self.history = history
        self.page_size = page_size
        self.day_title = day_title
        self.items: list[dict] = []
        self.query = ""
        self._last_id: Optional[int] = None
        self._last_day = None
        self._more = True
        self.expanded: set[int] = set()

    # ------------------------------------------------------------ данные
    def reload(self, query: str = "") -> None:
        self.beginResetModel()
        self.items, self.query = [], query
        self._last_id, self._last_day, self._more = None, None, True
        self.items = self._page()
        self.endResetModel()

    def _page(self) -> list[dict]:
        rows = self.history.recent(self.page_size + 1, self.query, self._last_id)
        self._more = len(rows) > self.page_size
        rows = rows[: self.page_size]
        out = []
        for row in rows:
            day = dt.datetime.fromtimestamp(row["ts"]).date()
            if day != self._last_day:
                self._last_day = day
                out.append({"kind": "day", "title": self.day_title(day).upper()})
            out.append({"kind": "item", "id": row["id"], "ts": row["ts"], "text": row["text"] or "(пусто)",
                        "audio": row["audio"] if row["audio"] and os.path.exists(row["audio"]) else None})
        if rows:
            self._last_id = rows[-1]["id"]
        return out

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.UserRole:
            return item
        if role == Qt.DisplayRole:
            return item.get("text") or item.get("title")
        return None

    def canFetchMore(self, parent=QModelIndex()) -> bool:  # noqa: N802
        return not parent.isValid() and self._more

    def fetchMore(self, parent=QModelIndex()) -> None:  # noqa: N802
        if parent.isValid() or not self._more:
            return
        new = self._page()
        if not new:
            return
        self.beginInsertRows(QModelIndex(), len(self.items), len(self.items) + len(new) - 1)
        self.items.extend(new)
        self.endInsertRows()

    def remove_id(self, row_id: int) -> None:
        for i, item in enumerate(self.items):
            if item.get("id") == row_id:
                first, last = i, i
                # Заголовок дня, под которым больше ничего нет, — тоже убрать.
                if i > 0 and self.items[i - 1]["kind"] == "day" and \
                        (i + 1 >= len(self.items) or self.items[i + 1]["kind"] == "day"):
                    first = i - 1
                self.beginRemoveRows(QModelIndex(), first, last)
                del self.items[first:last + 1]
                self.endRemoveRows()
                return

    def has_new(self) -> bool:
        """Появились записи новее показанных (или список пуст)?"""
        top = next((i["id"] for i in self.items if i["kind"] == "item"), None)
        newest = self.history.recent(1)
        if not newest:
            return top is not None
        return top is None or newest[0]["id"] > top

    def is_empty(self) -> bool:
        return not self.items


class HistoryDelegate(QStyledItemDelegate):
    """Рисует заголовки дней и карточки; кнопки карточки — нарисованные ссылки."""

    def __init__(self, view: "HistoryList"):
        super().__init__(view)
        self.view = view
        self._sizes: dict = {}

    # ------------------------------------------------------------ геометрия
    def _fonts(self) -> tuple[QFont, QFont, QFont]:
        base = QFont(self.view.font())
        small = QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() * 0.92))
        head = QFont(base)
        head.setPointSizeF(max(7.0, base.pointSizeF() * 0.8))
        head.setWeight(QFont.DemiBold)
        head.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        return base, small, head

    def _shown_text(self, item: dict) -> tuple[str, bool]:
        text = item["text"]
        if len(text) > LONG and item["id"] not in self.view.model().expanded:
            return text[:LONG].rstrip() + "…", True
        return text, False

    def _text_width(self) -> int:
        return max(120, self.view.viewport().width() - 2 * PAD_X - 4)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        item = index.data(Qt.UserRole)
        if not item:
            return QSize(0, 0)
        width = self._text_width()
        if item["kind"] == "day":
            return QSize(width, 34)
        text, cut = self._shown_text(item)
        key = (item["id"], width, cut, len(text))
        if key not in self._sizes:
            base, small, _ = self._fonts()
            fm = QFontMetrics(base)
            h = fm.boundingRect(QRect(0, 0, width, 1_000_000), Qt.TextWordWrap, text).height()
            if cut:
                h += QFontMetrics(small).height() + 6
            self._sizes[key] = PAD_Y + META_H + 4 + h + PAD_Y + 2 + GAP
        return QSize(width, self._sizes[key])

    def _card(self, rect: QRect) -> QRect:
        return rect.adjusted(2, 0, -2, -GAP)

    def action_rects(self, rect: QRect, item: dict) -> list[tuple[str, QRect]]:
        """Где нарисованы кнопки карточки (справа в строке времени)."""
        _, small, _ = self._fonts()
        fm = QFontMetrics(small)
        card = self._card(rect)
        labels = ["Копировать"] + (["▶ Слушать"] if item.get("audio") else []) + ["Удалить"]
        x = card.right() - PAD_X
        out = []
        for label in reversed(labels):
            w = fm.horizontalAdvance(label) + 12
            out.append((label, QRect(x - w, card.top() + PAD_Y, w, META_H)))
            x -= w
        return list(reversed(out))

    def more_rect(self, rect: QRect) -> QRect:
        _, small, _ = self._fonts()
        card = self._card(rect)
        h = QFontMetrics(small).height() + 6
        return QRect(card.left() + PAD_X, card.bottom() - PAD_Y - h, 200, h)

    # ------------------------------------------------------------ рисование
    def paint(self, painter: QPainter, option, index) -> None:
        item = index.data(Qt.UserRole)
        if not item:
            return
        base, small, head = self._fonts()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect
        if item["kind"] == "day":
            painter.setFont(head)
            painter.setPen(W.pc("muted"))
            painter.drawText(rect.adjusted(14, 0, 0, -6), Qt.AlignLeft | Qt.AlignBottom, item["title"])
            painter.restore()
            return
        card = self._card(rect)
        hover = bool(option.state & QStyle.State_MouseOver)
        path = QPainterPath()
        path.addRoundedRect(QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.fillPath(path, W.pc("hover") if hover else W.pc("card"))
        painter.setPen(QPen(W.pc("card_border"), 1))
        painter.drawPath(path)
        # Время
        painter.setFont(small)
        painter.setPen(W.pc("muted"))
        meta = QRect(card.left() + PAD_X, card.top() + PAD_Y, card.width() - 2 * PAD_X, META_H)
        painter.drawText(meta, Qt.AlignLeft | Qt.AlignVCenter,
                         dt.datetime.fromtimestamp(item["ts"]).strftime("%H:%M"))
        if hover:
            pos = self.view.viewport().mapFromGlobal(self.view.cursor().pos())
            for label, r in self.action_rects(rect, item):
                color = W.pc("danger") if label == "Удалить" else W.pc("accent")
                if label == "Копировать" and item["id"] == self.view.copied_id:
                    label = "Скопировано ✓"
                if r.contains(pos):
                    color = color.lighter(120)
                painter.setPen(color)
                painter.drawText(r, Qt.AlignCenter, label)
        # Текст
        text, cut = self._shown_text(item)
        painter.setFont(base)
        painter.setPen(W.pc("text"))
        body = QRect(card.left() + PAD_X, card.top() + PAD_Y + META_H + 4, self._text_width(), 1_000_000)
        painter.drawText(body, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text)
        if cut:
            painter.setFont(small)
            painter.setPen(W.pc("accent"))
            painter.drawText(self.more_rect(rect), Qt.AlignLeft | Qt.AlignVCenter, "Показать полностью")
        painter.restore()

    def clear_cache(self) -> None:
        self._sizes.clear()


class HistoryList(QListView):
    """Список истории. Сигналы — действия над записью (id, путь к звуку)."""

    copy_requested = Signal(int)
    play_requested = Signal(str)
    delete_requested = Signal(int)

    def __init__(self, model: HistoryModel, parent=None):
        super().__init__(parent)
        self.copied_id: Optional[int] = None
        self.setModel(model)
        self.setItemDelegate(HistoryDelegate(self))
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QListView { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def model(self) -> HistoryModel:  # type: ignore[override]
        return super().model()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.itemDelegate().clear_cache()
        super().resizeEvent(event)

    def _item_at(self, pos):
        index = self.indexAt(pos)
        item = index.data(Qt.UserRole) if index.isValid() else None
        return index, item

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        index, item = self._item_at(event.position().toPoint())
        hit = False
        if item and item["kind"] == "item":
            delegate = self.itemDelegate()
            rect = self.visualRect(index)
            hit = any(r.contains(event.position().toPoint()) for _, r in delegate.action_rects(rect, item))
            hit = hit or (len(item["text"]) > LONG and delegate.more_rect(rect).contains(event.position().toPoint()))
        self.viewport().setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        self.viewport().update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.viewport().update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index, item = self._item_at(pos)
        if event.button() == Qt.LeftButton and item and item["kind"] == "item":
            delegate = self.itemDelegate()
            rect = self.visualRect(index)
            for label, r in delegate.action_rects(rect, item):
                if r.contains(pos):
                    self._act(label, item)
                    return
            if len(item["text"]) > LONG and delegate.more_rect(rect).contains(pos):
                self.model().expanded.add(item["id"])
                delegate.clear_cache()
                self.doItemsLayout()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        _, item = self._item_at(event.position().toPoint())
        if item and item["kind"] == "item":
            self._act("Копировать", item)

    def _act(self, label: str, item: dict) -> None:
        if label == "Копировать":
            self.copied_id = item["id"]
            self.copy_requested.emit(item["id"])
            self.viewport().update()
            QTimer.singleShot(1200, self._uncopied)
        elif label.endswith("Слушать") and item.get("audio"):
            self.play_requested.emit(item["audio"])
        elif label == "Удалить":
            self.delete_requested.emit(item["id"])

    def _uncopied(self) -> None:
        self.copied_id = None
        self.viewport().update()

    def _menu(self, pos) -> None:
        _, item = self._item_at(pos)
        if not item or item["kind"] != "item":
            return
        menu = QMenu(self)
        menu.addAction("Копировать", lambda: self._act("Копировать", item))
        if item.get("audio"):
            menu.addAction("▶ Слушать", lambda: self._act("▶ Слушать", item))
        menu.addSeparator()
        menu.addAction("Удалить", lambda: self._act("Удалить", item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def event(self, e) -> bool:
        if e.type() == QEvent.Leave:
            self.viewport().setCursor(Qt.ArrowCursor)
        return super().event(e)
