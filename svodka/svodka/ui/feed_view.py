"""Лента без тормозов: QListView + свой делегат (рисуются только видимые строки, как история Aqua).

Строки: подпись раздела («ГЛАВНОЕ», «ЕЩЁ», «ВЧЕРА, 5 ОКТЯБРЯ») и статья. У статей «Главного» —
выжимка; у остальных — заголовок. При наведении — действия («Интересно», «Не моё», «Сохранить», «⋯»)
и строка «почему здесь».

Учёт показов (то, на чём учатся рекомендации): статья считается показанной, если не меньше
секунды была на экране целиком (≥60% высоты) при активном окне. Запоминается позиция и как
ранжирование её оценило — для честной подстройки весов.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListView, QStyledItemDelegate

from ..rank.signals import expected_ms
from . import widgets as W

PAD_X, PAD_Y = 16, 12
GAP = 4
META_H = 22
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def age_text(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return ""
    now = now or time.time()
    sec = max(0, now - float(ts))
    if sec < 3600:
        return f"{max(1, int(sec // 60))} мин"
    if sec < 86400:
        return f"{int(sec // 3600)} ч"
    days = int(sec // 86400)
    if days == 1:
        return "вчера"
    if days < 5:
        return f"{days} дня"
    return f"{days} дней"


def minutes_text(words: int) -> str:
    if not words:
        return ""
    return f"{max(1, round(expected_ms(words) / 60000))} мин"


def day_title(day: str) -> str:
    d = dt.date.fromisoformat(day)
    today = dt.date.today()
    if d == today:
        return "СЕГОДНЯ"
    if d == today - dt.timedelta(days=1):
        return f"ВЧЕРА, {d.day} {MONTHS[d.month - 1]}".upper()
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}".upper()


class FeedModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.expanded: set[int] = set()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.UserRole:
            return None
        return self.rows[index.row()]

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def remove_article(self, article_id: int) -> None:
        for i, r in enumerate(self.rows):
            if r["kind"] == "item" and r["item"].id == article_id:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()
                return

    def refresh_article(self, article_id: int, **fields) -> None:
        for i, r in enumerate(self.rows):
            if r["kind"] == "item" and r["item"].id == article_id:
                r["item"].article.update(fields)
                idx = self.index(i)
                self.dataChanged.emit(idx, idx)


def feed_rows(feed, main_title: str = "ГЛАВНОЕ", more_title: str = "ЕЩЁ") -> list[dict]:
    rows: list[dict] = []
    pos = 0
    if feed.main:
        rows.append({"kind": "section", "title": main_title})
        for it in feed.main:
            rows.append({"kind": "item", "item": it, "big": True, "position": pos})
            pos += 1
    if feed.more:
        rows.append({"kind": "section", "title": more_title})
        for it in feed.more:
            rows.append({"kind": "item", "item": it, "big": False, "position": pos})
            pos += 1
    for day, items in feed.days:
        rows.append({"kind": "section", "title": day_title(day)})
        for it in items:
            rows.append({"kind": "item", "item": it, "big": False, "position": pos})
            pos += 1
    return rows


def item_rows(items: list, big: bool = False, title: str = "") -> list[dict]:
    rows = [{"kind": "section", "title": title}] if title else []
    for pos, it in enumerate(items):
        rows.append({"kind": "item", "item": it, "big": big, "position": pos})
    return rows


class FeedDelegate(QStyledItemDelegate):
    def __init__(self, view: "FeedList"):
        super().__init__(view)
        self.view = view
        self._sizes: dict = {}

    def fonts(self) -> tuple[QFont, QFont, QFont, QFont]:
        base = QFont(self.view.font())
        title = QFont(base)
        title.setPointSizeF(base.pointSizeF() * 1.24)
        title.setWeight(QFont.DemiBold)
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() * 0.88)
        head = QFont(base)
        head.setPointSizeF(base.pointSizeF() * 0.85)
        head.setWeight(QFont.DemiBold)
        head.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        return base, title, small, head

    def text_width(self) -> int:
        return max(200, self.view.viewport().width() - 2 * PAD_X - 8)

    def summary_text(self, row: dict) -> str:
        a = row["item"].article
        bullets = a.get("summary_ru") or []
        if not bullets:
            return ""
        if row["item"].id in self.view.model().expanded:
            return "\n".join("•  " + b for b in bullets)
        return bullets[0] + ("   Коротко ▾" if len(bullets) > 1 else "")

    def show_why(self, row: dict) -> bool:
        return row["item"].id == self.view.hover_id and bool(row["item"].reasons)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        row = index.data(Qt.UserRole)
        if not row:
            return QSize(0, 0)
        width = self.text_width()
        if row["kind"] == "section":
            return QSize(width, 38)
        a = row["item"].article
        expanded = row["item"].id in self.view.model().expanded
        key = (row["item"].id, width, row["big"], expanded, self.show_why(row), a.get("status"))
        if key not in self._sizes:
            base, title, small, _ = self.fonts()
            h = PAD_Y + META_H
            title_text = a.get("title_ru") or a.get("title_orig") or ""
            h += QFontMetrics(title).boundingRect(QRect(0, 0, width, 100000), Qt.TextWordWrap, title_text).height()
            if row["big"]:
                s = self.summary_text(row)
                if s:
                    h += 4 + QFontMetrics(base).boundingRect(QRect(0, 0, width, 100000), Qt.TextWordWrap, s).height()
            if self.show_why(row):
                h += 4 + QFontMetrics(small).height()
            h += PAD_Y + GAP
            self._sizes[key] = h
        return QSize(width, self._sizes[key])

    def card_rect(self, rect: QRect) -> QRect:
        return rect.adjusted(2, 0, -2, -GAP)

    def action_rects(self, rect: QRect, row: dict) -> list[tuple[str, str, QRect]]:
        """(ключ, подпись, область) кнопок справа в строке мета — только при наведении."""
        _, _, small, _ = self.fonts()
        fm = QFontMetrics(small)
        card = self.card_rect(rect)
        a = row["item"].article
        labels = [("like", "Интересно ✓" if a.get("_liked") else "Интересно"), ("dislike", "Не моё"),
                  ("save", "Сохранено" if a.get("saved") else "Сохранить"), ("more", "⋯")]
        x = card.right() - PAD_X + 6
        out = []
        for key, label in reversed(labels):
            w = fm.horizontalAdvance(label) + 14
            out.append((key, label, QRect(x - w, card.top() + PAD_Y - 2, w, META_H)))
            x -= w
        return list(reversed(out))

    def summary_rect(self, rect: QRect, row: dict) -> QRect | None:
        if not row["big"]:
            return None
        base, title, _, _ = self.fonts()
        card = self.card_rect(rect)
        width = self.text_width()
        a = row["item"].article
        title_h = QFontMetrics(title).boundingRect(QRect(0, 0, width, 100000), Qt.TextWordWrap,
                                                   a.get("title_ru") or a.get("title_orig") or "").height()
        top = card.top() + PAD_Y + META_H + title_h + 4
        s = self.summary_text(row)
        h = QFontMetrics(base).boundingRect(QRect(0, 0, width, 100000), Qt.TextWordWrap, s).height()
        return QRect(card.left() + PAD_X, top, width, h)

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.data(Qt.UserRole)
        if not row:
            return
        base, title_font, small, head = self.fonts()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect
        if row["kind"] == "section":
            painter.setFont(head)
            painter.setPen(W.pc("muted"))
            painter.drawText(rect.adjusted(PAD_X, 0, 0, -8), Qt.AlignLeft | Qt.AlignBottom, row["title"])
            painter.restore()
            return
        it = row["item"]
        a = it.article
        card = self.card_rect(rect)
        hover = it.id == self.view.hover_id
        if hover or self.view.current_id == it.id:
            path = QPainterPath()
            path.addRoundedRect(card, 10, 10)
            painter.fillPath(path, W.pc("hover"))
        read = a.get("status") == "read"
        x = card.left() + PAD_X
        y = card.top() + PAD_Y
        width = self.text_width()
        # строка мета: тема · источник · возраст · минуты [· Разведка]
        painter.setFont(small)
        fm = QFontMetrics(small)
        cx = x
        topic = a.get("topic") or ""
        if getattr(it, "novel_topic", it.explore):
            badge = "Разведка"
            bw = fm.horizontalAdvance(badge) + 14
            bpath = QPainterPath()
            bpath.addRoundedRect(cx, y + 2, bw, META_H - 6, 8, 8)
            painter.fillPath(bpath, W.pc("accent_soft"))
            painter.setPen(W.pc("accent"))
            painter.drawText(QRect(cx, y, bw, META_H - 2), Qt.AlignCenter, badge)
            cx += bw + 8
        if topic:
            painter.setPen(W.pc("muted") if read else W.pc("accent"))
            painter.drawText(QRect(cx, y, width, META_H), Qt.AlignLeft | Qt.AlignVCenter, topic)
            cx += fm.horizontalAdvance(topic)
        meta = [a.get("source") or a.get("domain") or "",
                age_text(a.get("published_at") or a.get("collected_at")), minutes_text(int(a.get("words") or 0))]
        if a.get("saved"):
            meta.append("в закладках")
        meta_text = "".join(f"  ·  {m}" for m in meta if m) if topic else "  ·  ".join(m for m in meta if m)
        painter.setPen(W.pc("muted"))
        actions = self.action_rects(rect, row) if hover else []
        right_limit = (actions[0][2].left() - 8) if actions else card.right() - PAD_X
        painter.drawText(QRect(cx, y, max(0, right_limit - cx), META_H), Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(meta_text, Qt.ElideRight, max(0, right_limit - cx)))
        for key, label, r in actions:
            on = (key == "save" and a.get("saved")) or (key == "like" and a.get("_liked"))
            painter.setPen(W.pc("accent") if on else W.pc("text_soft"))
            painter.drawText(r, Qt.AlignCenter, label)
        y += META_H
        # заголовок
        painter.setFont(title_font)
        painter.setPen(W.pc("muted") if read else W.pc("text"))
        t = a.get("title_ru") or a.get("title_orig") or ""
        tr = QFontMetrics(title_font).boundingRect(QRect(x, y, width, 100000), Qt.TextWordWrap, t)
        painter.drawText(QRect(x, y, width, tr.height()), Qt.TextWordWrap, t)
        y += tr.height()
        if row["big"]:
            s = self.summary_text(row)
            if s:
                y += 4
                painter.setFont(base)
                painter.setPen(W.pc("faint") if read else W.pc("text_soft"))
                sr = QFontMetrics(base).boundingRect(QRect(x, y, width, 100000), Qt.TextWordWrap, s)
                painter.drawText(QRect(x, y, width, sr.height()), Qt.TextWordWrap, s)
                y += sr.height()
        if self.show_why(row):
            y += 4
            painter.setFont(small)
            painter.setPen(W.pc("muted"))
            painter.drawText(QRect(x, y, width, QFontMetrics(small).height()), Qt.AlignLeft,
                             fm.elidedText("Почему здесь: " + " · ".join(it.reasons), Qt.ElideRight, width))
        painter.restore()


class FeedList(QListView):
    """Сигналы: open_article(id), action(id, ключ, глобальная точка)."""

    open_article = Signal(int)
    action = Signal(int, str, object)
    expanded = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(FeedModel())
        self.delegate = FeedDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setUniformItemSizes(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QListView { background: transparent; border: none; }")
        self.hover_id: Optional[int] = None
        self.current_id: Optional[int] = None
        self.on_impression = None          # функция (article_id, позиция, мс, item) — задаёт окно
        self.track_visible_ms: dict[int, int] = {}
        self.logged: set[int] = set()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._track)
        self._tick.start(500)

    def set_rows(self, rows: list[dict]) -> None:
        self.delegate._sizes.clear()
        self.track_visible_ms.clear()
        self.logged.clear()
        self.model().set_rows(rows)

    def rows(self) -> list[dict]:
        return self.model().rows

    def row_at(self, pos) -> tuple[Optional[dict], QRect]:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return None, QRect()
        return idx.data(Qt.UserRole), self.visualRect(idx)

    def _relayout(self) -> None:
        self.delegate._sizes.clear()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        row, _ = self.row_at(event.position().toPoint())
        new = row["item"].id if row and row["kind"] == "item" else None
        if new != self.hover_id:
            self.hover_id = new
            self._relayout()
        self.setCursor(Qt.PointingHandCursor if new else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.hover_id is not None:
            self.hover_id = None
            self._relayout()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = event.position().toPoint()
        row, rect = self.row_at(pos)
        if not row or row["kind"] != "item":
            return
        aid = row["item"].id
        for key, _label, r in self.delegate.action_rects(rect, row):
            if r.contains(pos):
                self.action.emit(aid, key, self.viewport().mapToGlobal(r.bottomLeft()))
                return
        sr = self.delegate.summary_rect(rect, row)
        if sr is not None and sr.contains(pos) and len(row["item"].article.get("summary_ru") or []) > 1:
            model = self.model()
            if aid in model.expanded:
                model.expanded.discard(aid)
            else:
                model.expanded.add(aid)
                self.expanded.emit(aid)
            self._relayout()
            return
        self.open_article.emit(aid)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.delegate._sizes.clear()
        super().resizeEvent(event)

    # ------------------------------------------------------------- учёт показов
    def visible_rows(self) -> list[tuple[dict, float]]:
        """(строка, доля видимой высоты) для строк статей на экране."""
        out = []
        vp = self.viewport().rect()
        idx = self.indexAt(vp.topLeft())
        row_i = idx.row() if idx.isValid() else 0
        model = self.model()
        while 0 <= row_i < model.rowCount():
            index = model.index(row_i)
            r = self.visualRect(index)
            if r.top() > vp.bottom():
                break
            row = model.rows[row_i]
            if row["kind"] == "item" and r.height() > 0:
                visible = r.intersected(vp)
                out.append((row, visible.height() / r.height()))
            row_i += 1
        return out

    def _track(self) -> None:
        win = self.window()
        if not self.isVisible() or win is None or not win.isActiveWindow() or self.on_impression is None:
            return
        for row, share in self.visible_rows():
            if share < 0.6:
                continue
            aid = row["item"].id
            ms = self.track_visible_ms.get(aid, 0) + 500
            self.track_visible_ms[aid] = ms
            if ms >= 1000 and aid not in self.logged:
                self.logged.add(aid)
                try:
                    self.on_impression(aid, row["position"], ms, row["item"])
                except Exception:  # noqa: BLE001 — учёт не должен ломать ленту
                    pass
