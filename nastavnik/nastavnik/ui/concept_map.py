"""Карта темы: понятия как схема, раскрашенная по освоенности (вы любите схемы — прогресс тоже схема).

Слои сверху вниз — по глубине зависимостей: наверху то, с чего начинается тема, ниже — то,
что на нём держится. Связи — плавные кривые от предварительного понятия к следующему.
Рисуется через QPainter, без картинок; подсказка при наведении — суть понятия.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import widgets as W

NODE_W = 132
NODE_H = 44
ROW_GAP = 34
STATUS_LABELS = {"new": "впереди", "learning": "пройдено, закрепляется", "mastered": "закреплено"}


def layout(concepts: list[dict], width: int) -> tuple[dict[str, QRectF], int]:
    """Разложить понятия по слоям. Возвращает {slug: прямоугольник} и высоту."""
    by_slug = {c["slug"]: c for c in concepts}
    depth: dict[str, int] = {}

    def d(slug: str, seen: frozenset = frozenset()) -> int:
        if slug in depth:
            return depth[slug]
        c = by_slug.get(slug)
        if c is None or slug in seen:
            return 0
        pre = [p for p in c["prereqs"] if p in by_slug]
        depth[slug] = 0 if not pre else 1 + max(d(p, seen | {slug}) for p in pre)
        return depth[slug]

    for c in concepts:
        d(c["slug"])
    per_row = max(1, (width + 16) // (NODE_W + 16))
    rows: list[list[str]] = []
    for level in range(max(depth.values(), default=-1) + 1):
        slugs = [c["slug"] for c in concepts if depth.get(c["slug"]) == level]
        for k in range(0, len(slugs), per_row):
            rows.append(slugs[k:k + per_row])
    rects: dict[str, QRectF] = {}
    y = 8.0
    for row in rows:
        total = len(row) * NODE_W + (len(row) - 1) * 16
        x = (width - total) / 2
        for slug in row:
            rects[slug] = QRectF(x, y, NODE_W, NODE_H)
            x += NODE_W + 16
        y += NODE_H + ROW_GAP
    return rects, int(y - ROW_GAP + 10) if rows else 0


class ConceptMap(QWidget):
    concept_clicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.concepts: list[dict] = []
        self.rects: dict[str, QRectF] = {}
        self._h = 60
        self.setMouseTracking(True)
        self._hover = ""

    def set_concepts(self, concepts: list[dict]) -> None:
        self.concepts = concepts
        self._relayout()

    def _relayout(self) -> None:
        self.rects, self._h = layout(self.concepts, max(200, self.width()))
        self.setMinimumHeight(max(60, self._h))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, max(60, self._h))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _at(self, pos) -> dict | None:
        for c in self.concepts:
            r = self.rects.get(c["slug"])
            if r is not None and r.contains(QPointF(pos)):
                return c
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        c = self._at(event.position().toPoint())
        slug = c["slug"] if c else ""
        if slug != self._hover:
            self._hover = slug
            self.setCursor(Qt.PointingHandCursor if c else Qt.ArrowCursor)
            self.update()
        if c:
            tip = f"<b>{c['title']}</b> · {STATUS_LABELS.get(c['status'], '')}"
            if c.get("summary"):
                tip += f"<br>{c['summary']}"
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        c = self._at(event.position().toPoint())
        if c:
            self.concept_clicked.emit(int(c["id"]))

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hair = W.pc("muted")
        hair.setAlpha(110)
        p.setPen(QPen(hair, 1.4))
        p.setBrush(Qt.NoBrush)
        for c in self.concepts:
            to = self.rects.get(c["slug"])
            for pre in c["prereqs"]:
                fr = self.rects.get(pre)
                if fr is None or to is None:
                    continue
                a = QPointF(fr.center().x(), fr.bottom())
                b = QPointF(to.center().x(), to.top())
                path = QPainterPath(a)
                mid = (a.y() + b.y()) / 2
                path.cubicTo(QPointF(a.x(), mid), QPointF(b.x(), mid), b)
                p.drawPath(path)
        fm = QFontMetrics(self.font())
        for c in self.concepts:
            r = self.rects.get(c["slug"])
            if r is None:
                continue
            status = c["status"]
            if status == "mastered":
                fill, border, text = W.pc("success"), W.pc("success"), W.pc("text")
                fill.setAlpha(48)
            elif status == "learning":
                fill, border, text = W.pc("accent"), W.pc("accent"), W.pc("text")
                fill.setAlpha(46)
            else:
                fill, border, text = W.pc("card"), W.pc("card_border"), W.pc("muted")
            if self._hover == c["slug"]:
                border = W.pc("accent")
            p.setPen(Qt.NoPen)
            p.setBrush(W.pc("card_solid"))           # непрозрачная подложка: связи не просвечивают
            p.drawRoundedRect(r, 11, 11)
            p.setPen(QPen(border, 1.2))
            p.setBrush(fill)
            p.drawRoundedRect(r, 11, 11)
            p.setPen(text)
            title = fm.elidedText(c["title"], Qt.ElideRight, int(r.width() - 16))
            p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignCenter, title)
            if status == "mastered":
                p.setPen(QPen(W.pc("success"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                cx, cy = r.right() - 10, r.top() + 10
                p.drawLine(QPointF(cx - 4, cy), QPointF(cx - 1.5, cy + 2.5))
                p.drawLine(QPointF(cx - 1.5, cy + 2.5), QPointF(cx + 3.5, cy - 3))
        p.end()
