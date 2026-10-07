"""Доска для схем от руки (Ctrl+D в сессии): рисуете мышью или пером, а Claude получает структуру — Mermaid.

Перо (1) — главный инструмент, распознаётся всё само (правила — в nastavnik/sketch.py): обвели —
узел, провели от узла к узлу — стрелка, написали внутри — подпись. Пока ведёте линию, подсвечиваются
фигуры, к которым она привяжется. Выбор (2) двигает узлы — стрелки тянутся следом; конец стрелки
можно перетащить на другой узел. Ластик (3). Текст (4) — подписи с клавиатуры; после новой фигуры
поле подписи открывается само, двойной щелчок — подписать что угодно.
Внизу — ровно то, что получит Claude; обновляется на каждом штрихе.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .. import sketch
from ..sketch import Board, point_at, path_len
from ..util import plural
from . import theme
from . import widgets as W

TOOLS = ("pen", "select", "eraser", "text")
TOOL_LABELS = ("Перо", "Выбор", "Ластик", "Текст")
TOOL_TIPS = ("Перо — клавиша 1. Обведите — узел, линия от узла к узлу — стрелка, внутри узла — подпись от руки",
             "Выбор — клавиша 2. Двигайте узлы (стрелки тянутся следом), тяните конец стрелки на другой узел",
             "Ластик — клавиша 3. Сначала стирает подписи от руки, потом стрелки, потом фигуры (по контуру)",
             "Текст — клавиша 4. Щёлкните по узлу или стрелке — подпись с клавиатуры, по пустому месту — надпись")
HINT = ("Обведите — получится узел. Проведите линию от узла к узлу — стрелка.\n"
        "Пишите в узле от руки или двойной щелчок — подпись с клавиатуры.")


# ================================================================ цвета и рисование
def _qc(value) -> QColor:
    if isinstance(value, QColor):
        return QColor(value)
    if isinstance(value, str) and value.startswith("rgba("):
        r, g, b, a = [int(x) for x in value[5:-1].split(",")]
        return QColor(r, g, b, a)
    return QColor(value)


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


def colors(pal: dict | None = None) -> dict[str, QColor]:
    """Цвета доски из палитры окна (или светлые — для картинки, которую получит Claude)."""
    pal = pal or W.PALETTE or theme.LIGHT
    dark = pal.get("name") == "dark"
    c = {k: _qc(pal[k]) for k in ("text", "muted", "faint", "accent", "warn", "card_solid")}
    return {"bg": c["card_solid"], "grid": _alpha(c["faint"], 85), "ink": QColor("#E4E9F2" if dark else "#1B2130"),
            "free": _alpha(c["muted"], 235), "fill": _alpha(c["accent"], 24 if dark else 15),
            "group_fill": _alpha(c["text"], 7 if dark else 5), "group": c["muted"], "label": c["text"],
            "accent": c["accent"], "warn": c["warn"], "muted": c["muted"]}


def smooth_path(pts, closed: bool = False) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    if len(pts) < 3:
        for q in pts[1:]:
            path.lineTo(QPointF(*q))
        return path
    for i in range(1, len(pts) - 1):
        mid = ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
        path.quadTo(QPointF(*pts[i]), QPointF(*mid))
    path.lineTo(QPointF(*pts[-1]))
    if closed:
        path.closeSubpath()
    return path


def shape_path(s: sketch.Shape) -> QPainterPath:
    r = QRectF(s.x, s.y, s.w, s.h)
    path = QPainterPath()
    if s.kind == "ellipse" and not s.ink and s.w > 1.6 * s.h:     # ровный овал из Mermaid — «капсула», как там
        path.addRoundedRect(r, s.h / 2, s.h / 2)
    elif s.kind in ("ellipse", "circle"):
        path.addEllipse(r)
    elif s.kind == "diamond":
        path.addPolygon(QPolygonF([QPointF(s.cx, s.y), QPointF(s.x + s.w, s.cy), QPointF(s.cx, s.y + s.h),
                                   QPointF(s.x, s.cy)]))
        path.closeSubpath()
    elif s.kind == "round":
        path.addRoundedRect(r, min(s.h / 2, 18), min(s.h / 2, 18))
    else:
        path.addRoundedRect(r, 6, 6)
    return path


def _fill_path(s: sketch.Shape) -> QPainterPath:
    if s.ink:
        pts = [(s.x + p[0], s.y + p[1]) for p in s.ink[0]]
        return smooth_path(pts, closed=True)
    return shape_path(s)


def _font(size: float = 10.2, bold: bool = False) -> QFont:
    f = QFont(QApplication.font())
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def measure_label(text: str) -> tuple[float, float]:
    """Ширина и высота подписи шрифтом доски (перенос строк — после 170 px)."""
    fm = QFontMetricsF(_font())
    r = fm.boundingRect(QRectF(0, 0, 170, 2000), int(Qt.TextWordWrap | Qt.AlignCenter), text or " ")
    return r.width() + 2, r.height()


def schema_from_mermaid(text: str) -> Board | None:
    """Схема Claude из Mermaid, разложенная с точной шириной подписей."""
    board = sketch.from_mermaid(text, measure_label)
    return board if board is not None and board.shapes else None


def _head(p: QPainter, pts, at_end: bool) -> None:
    length = path_len(pts)
    if length < 4:
        return
    tip = pts[-1] if at_end else pts[0]
    back = point_at(pts, max(0.0, (length - 14) / length)) if at_end else point_at(pts, min(1.0, 14 / length))
    ang = math.atan2(tip[1] - back[1], tip[0] - back[0])
    for d in (0.48, -0.48):
        p.drawLine(QPointF(*tip), QPointF(tip[0] - 12 * math.cos(ang + d), tip[1] - 12 * math.sin(ang + d)))


def _edge_label(p: QPainter, b: Board, e: sketch.Edge, col: dict, font: QFont) -> None:
    text = e.label.strip()
    if not text:
        return
    m = b.edge_mid(e)
    fm = QFontMetricsF(font)
    w = min(220.0, fm.horizontalAdvance(text) + 12)
    r = QRectF(m[0] - w / 2, m[1] - 11, w, 22)
    p.setPen(Qt.NoPen)
    p.setBrush(col["bg"])
    p.drawRoundedRect(r, 6, 6)
    p.setPen(col["label"])
    p.setFont(font)
    p.drawText(r, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, w - 8))


def paint_board(p: QPainter, b: Board, col: dict, sel=frozenset(), hot=(), flash: dict | None = None,
                handles: bool = True) -> None:
    """Вся доска в координатах доски (масштаб и сдвиг задаёт вызывающий)."""
    p.setRenderHint(QPainter.Antialiasing)
    font, gfont = _font(), _font(9.4, bold=True)
    pm = b.parent_map()
    groups = b.group_ids(pm)
    flash = flash or {}
    ink_pen = QPen(col["ink"], 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    shapes = sorted(b.shapes.values(), key=lambda s: -s.area)

    def overlay(path: QPainterPath, key, width=5.0) -> None:
        a = 0
        if key in sel:
            a = 90
        if key in flash:
            a = max(a, int(160 * flash[key]))
        if key in hot:
            a = max(a, 110)
        if a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_alpha(col["accent"], a), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)

    # группы — подложка и контур под всем остальным
    for s in shapes:
        if s.id not in groups:
            continue
        p.setPen(Qt.NoPen)
        p.setBrush(col["group_fill"])
        p.drawPath(_fill_path(s))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col["group"], 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in s.outline():
            p.drawPath(smooth_path(st) if s.ink else shape_path(s))
            if not s.ink:
                break
        overlay(shape_path(s) if not s.ink else smooth_path(s.outline()[0]), ("shape", s.id))
        if s.label:
            p.setPen(col["muted"])
            p.setFont(gfont)
            p.drawText(QRectF(s.x + 10, s.y + 4, s.w - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, s.label)
        p.setPen(QPen(col["ink"], 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in s.hand_abs():
            p.drawPath(smooth_path(st))
    # стрелки
    for e in b.edges.values():
        pts = b.edge_path(e)
        path = smooth_path(pts)
        overlay(path, ("edge", e.id), 6.0)
        pen = QPen(ink_pen)
        if e.style == "dotted":
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([3.0, 3.0])
        elif e.style == "thick":
            pen.setWidthF(3.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(col["ink"], pen.widthF(), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if e.head_b:
            _head(p, pts, True)
        if e.head_a:
            _head(p, pts, False)
        p.setPen(QPen(col["ink"], 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in b.edge_hand_abs(e):
            p.drawPath(smooth_path(st))
        _edge_label(p, b, e, col, font)
        p.setPen(Qt.NoPen)
        p.setBrush(col["warn"])
        for free, end in ((e.a is None, pts[0]), (e.b is None, pts[-1])):
            if free:                                    # свободный конец — привязать не к чему
                p.drawEllipse(QPointF(*end), 4.2, 4.2)
        if handles and ("edge", e.id) in sel:
            p.setBrush(col["bg"])
            p.setPen(QPen(col["accent"], 2))
            for end in (pts[0], pts[-1]):
                p.drawEllipse(QPointF(*end), 5.5, 5.5)
    # узлы
    for s in reversed(shapes):
        if s.id in groups:
            continue
        p.setPen(Qt.NoPen)
        p.setBrush(col["fill"])
        p.drawPath(_fill_path(s))
        overlay(_fill_path(s), ("shape", s.id))
        p.setBrush(Qt.NoBrush)
        p.setPen(ink_pen)
        if s.ink:
            for st in s.outline():
                p.drawPath(smooth_path(st))
        else:
            p.drawPath(shape_path(s))
        p.setPen(QPen(col["ink"], 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in s.hand_abs():
            p.drawPath(smooth_path(st))
        if s.label:
            p.setPen(col["label"])
            p.setFont(font)
            capsule = s.kind == "ellipse" and not s.ink and s.w > 1.6 * s.h
            inset = 0.2 if s.kind in ("ellipse", "circle", "diamond") and not capsule else 0.06
            r = QRectF(s.x + s.w * inset + 4, s.y + 2, s.w * (1 - 2 * inset) - 8, s.h - 4)
            if s.hand:                                  # и напечатано, и от руки: печатное — под рукописным
                r = QRectF(s.x + 6, s.y + s.h - 24, s.w - 12, 20)
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, s.label)
    # рисунок от руки без привязки и надписи
    p.setPen(QPen(col["free"], 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    for k in b.ink.values():
        path = smooth_path(k.pts)
        overlay(path, ("ink", k.id), 6.0)
        p.setPen(QPen(col["free"], 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    p.setFont(font)
    fm = QFontMetricsF(font)
    for n in b.notes.values():
        r = QRectF(n.x, n.y, fm.horizontalAdvance(n.text) + 4, 20)
        if ("note", n.id) in sel or ("note", n.id) in flash:
            p.setPen(Qt.NoPen)
            p.setBrush(_alpha(col["accent"], 50))
            p.drawRoundedRect(r.adjusted(-4, -2, 4, 2), 5, 5)
        p.setPen(col["label"])
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, n.text)


def render_image(b: Board, max_w: int = 1400, pal: dict | None = None, scale: float = 1.5,
                 margin: float = 28) -> QImage:
    """Доска картинкой: для Claude (светлая) и миниатюр в чате (в цветах окна)."""
    bb = b.bounds() or (0, 0, 200, 120)
    w, h = bb[2] - bb[0] + 2 * margin, bb[3] - bb[1] + 2 * margin
    k = min(scale, max_w / max(w, 1))
    img = QImage(max(1, int(w * k)), max(1, int(h * k)), QImage.Format_ARGB32_Premultiplied)
    col = colors(pal or theme.LIGHT)
    img.fill(col["bg"])
    p = QPainter(img)
    p.scale(k, k)
    p.translate(margin - bb[0], margin - bb[1])
    paint_board(p, b, col, handles=False)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def describe_status(b: Board, image: bool) -> str:
    """Строка под доской: что распознано и что стоит поправить."""
    if b.is_empty():
        return "Доска пустая."
    st = b.stats()
    parts = [sketch.describe(b)]
    if st["dangling"]:
        parts.append(f"{st['dangling']} {plural(st['dangling'], ('стрелка', 'стрелки', 'стрелок'))} "
                     f"ни к чему не {'привязана' if st['dangling'] == 1 else 'привязаны'} (оранжевый конец)")
    if st["unnamed"]:
        parts.append(f"без подписи: {st['unnamed']}")
    if st["hand"] or st["ink"]:
        parts.append("подписи от руки Claude прочитает с картинки" if image
                     else "подписи от руки без картинки Claude не увидит")
    return " · ".join(parts)


# ================================================================ холст
class BoardCanvas(QWidget):
    """Холст: перо, выбор, ластик, текст; масштаб Ctrl+колесо, сдвиг — средняя кнопка или пробел."""

    changed = Signal()
    tool_changed = Signal(str)

    def __init__(self, board: Board | None = None):
        super().__init__()
        self.setObjectName("BoardCanvas")
        self.board = board or Board()
        self.tool = "pen"
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.sel: set[tuple[str, int]] = set()
        self.hot: set[tuple[str, int]] = set()
        self.flash: dict[tuple[str, int], float] = {}
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._stroke: list[tuple[float, float]] = []
        self._mode = ""                 # draw | move | end | band | erase | pan
        self._press = QPointF()
        self._last = QPointF()
        self._snap: dict | None = None
        self._moved = False
        self._drag_items: set = set()
        self._drag_end: tuple[int, str] | None = None
        self._band: QRectF | None = None
        self._cursor = QPointF(-100, -100)
        self._space = False
        self._erase_last = None
        self._editing: tuple[str, int] | None = None
        self._edit_snap: dict | None = None
        self.editor = QLineEdit(self)
        self.editor.setObjectName("BoardEditor")
        self.editor.hide()
        self.editor.returnPressed.connect(self.commit_edit)
        self.editor.installEventFilter(self)
        self._tick = QTimer(self)
        self._tick.setInterval(30)
        self._tick.timeout.connect(self._animate)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 240)
        self.setCursor(Qt.CrossCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ------------------------------------------------------------- координаты
    def to_world(self, pos) -> tuple[float, float]:
        return ((pos.x() - self.pan.x()) / self.zoom, (pos.y() - self.pan.y()) / self.zoom)

    def to_screen(self, p) -> QPointF:
        return QPointF(p[0] * self.zoom + self.pan.x(), p[1] * self.zoom + self.pan.y())

    def fit(self, max_zoom: float = 1.0) -> None:
        bb = self.board.bounds()
        if not bb or self.width() < 50:
            self.zoom, self.pan = 1.0, QPointF(0, 0)
        else:
            w, h = bb[2] - bb[0] + 60, bb[3] - bb[1] + 60
            self.zoom = max(0.35, min(max_zoom, self.width() / w, self.height() / h))
            self.pan = QPointF(self.width() / 2 - (bb[0] + bb[2]) / 2 * self.zoom,
                               self.height() / 2 - (bb[1] + bb[3]) / 2 * self.zoom)
        self.update()

    # ------------------------------------------------------------- данные, отмена
    def set_board(self, board: Board, fit: bool = True, keep_history: bool = False) -> None:
        self.cancel_edit()
        if keep_history:
            self.checkpoint()
        else:
            self.undo_stack.clear()
            self.redo_stack.clear()
        self.board = board
        self.sel.clear()
        if fit:
            self.fit()
        self.changed.emit()
        self.update()

    def checkpoint(self, snap: dict | None = None) -> None:
        self.undo_stack.append(snap if snap is not None else self.board.to_dict())
        del self.undo_stack[:-200]
        self.redo_stack.clear()

    def undo(self) -> None:
        self.cancel_edit()
        if self.undo_stack:
            self.redo_stack.append(self.board.to_dict())
            self.board = Board.from_dict(self.undo_stack.pop())
            self._after_history()

    def redo(self) -> None:
        self.cancel_edit()
        if self.redo_stack:
            self.undo_stack.append(self.board.to_dict())
            self.board = Board.from_dict(self.redo_stack.pop())
            self._after_history()

    def _after_history(self) -> None:
        self.sel = {k for k in self.sel if self._exists(k)}
        self.changed.emit()
        self.update()

    def clear(self) -> None:
        if not self.board.is_empty():
            self.checkpoint()
            self.board = Board()
            self.sel.clear()
            self.changed.emit()
            self.update()

    def _exists(self, key) -> bool:
        kind, i = key
        return i in {"shape": self.board.shapes, "edge": self.board.edges, "note": self.board.notes,
                     "ink": self.board.ink}.get(kind, {})

    def _changed(self) -> None:
        self.sel = {k for k in self.sel if self._exists(k)}
        self.changed.emit()
        self.update()

    # ------------------------------------------------------------- инструменты
    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            return
        self.commit_edit()
        self.tool = tool
        if tool != "select":
            self.sel.clear()
        self.setCursor({"pen": Qt.CrossCursor, "select": Qt.ArrowCursor, "eraser": Qt.BlankCursor,
                        "text": Qt.IBeamCursor}[tool])
        self.tool_changed.emit(tool)
        self.update()

    def _flash(self, key) -> None:
        self.flash[key] = time.time()
        if not self._tick.isActive():
            self._tick.start()

    def _animate(self) -> None:
        now = time.time()
        self.flash = {k: t for k, t in self.flash.items() if now - t < 0.7}
        if not self.flash:
            self._tick.stop()
        self.update()

    # ------------------------------------------------------------- подписи
    def edit_label(self, kind: str, item_id: int, at=None) -> None:
        self.commit_edit()
        b = self.board
        if kind == "shape" and item_id in b.shapes:
            s = b.shapes[item_id]
            text, center, width = s.label, (s.cx, s.cy), max(130.0, s.w * self.zoom - 12)
            if b.is_group(s):
                center = (s.x + s.w / 2, s.y + 14)
        elif kind == "edge" and item_id in b.edges:
            e = b.edges[item_id]
            text, center, width = e.label, b.edge_mid(e), 170.0
        elif kind == "note" and item_id in b.notes:
            n = b.notes[item_id]
            text, center, width = n.text, (n.x + 60, n.y + 10), 180.0
        else:
            return
        self._editing = (kind, item_id)
        self._edit_snap = b.to_dict()
        c = self.to_screen(center)
        w = int(min(width, 360))
        self.editor.setGeometry(int(c.x() - w / 2), int(c.y() - 15), w, 30)
        self.editor.setText(text)
        self.editor.setPlaceholderText({"shape": "подпись узла", "edge": "подпись стрелки",
                                        "note": "надпись"}[kind])
        self.editor.show()
        self.editor.setFocus()
        self.editor.end(False)          # без выделения: иначе Enter отдал бы подпись в PRIMARY

    def commit_edit(self) -> None:
        if self._editing is None:
            return
        kind, item_id = self._editing
        text = self.editor.text()
        self._editing = None
        self.editor.hide()
        before = self._edit_snap
        self.board.set_label(kind, item_id, text)
        if before is not None and before != self.board.to_dict():
            self.checkpoint(before)
        self.setFocus()
        self._changed()

    def cancel_edit(self) -> None:
        if self._editing is None:
            return
        kind, item_id = self._editing
        self._editing = None
        self.editor.hide()
        if kind == "note" and item_id in self.board.notes and not self.board.notes[item_id].text:
            self.board.delete("note", item_id)
        self.setFocus()
        self._changed()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.editor:
            if event.type() == event.Type.KeyPress and event.key() == Qt.Key_Escape:
                self.cancel_edit()
                return True
            if event.type() == event.Type.FocusOut and self._editing is not None:
                QTimer.singleShot(0, self.commit_edit)
        return False

    # ------------------------------------------------------------- мышь
    def _handle_at(self, pos) -> tuple[int, str] | None:
        for kind, i in self.sel:
            if kind == "edge" and i in self.board.edges:
                pts = self.board.edge_path(self.board.edges[i])
                for which, end in (("a", pts[0]), ("b", pts[-1])):
                    q = self.to_screen(end)
                    if math.hypot(q.x() - pos.x(), q.y() - pos.y()) <= 9:
                        return (i, which)
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.setFocus()
        if event.button() == Qt.MiddleButton or (self._space and event.button() == Qt.LeftButton):
            self._mode, self._last = "pan", pos
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() == Qt.RightButton:
            self.commit_edit()
            self._menu(event)
            return
        if event.button() != Qt.LeftButton:
            return
        self.commit_edit()
        w = self.to_world(pos)
        self._press, self._last, self._moved = pos, pos, False
        if self.tool == "pen":
            self._mode = "draw"
            self._stroke = [w]
            s = self.board.shape_at(w, sketch.SNAP / self.zoom)
            self.hot = {("shape", s.id)} if s else set()
        elif self.tool == "eraser":
            self._mode = "erase"
            self._snap = self.board.to_dict()
            self._erase_last = None
            self._erase(w)
        elif self.tool == "text":
            self._mode = ""
            self._text_at(w)
        else:
            h = self._handle_at(pos)
            if h:
                self._mode, self._drag_end = "end", h
                self._snap = self.board.to_dict()
                return
            key = self.board.hit(w, 8 / self.zoom)
            if key is None:
                if not event.modifiers() & Qt.ShiftModifier:
                    self.sel.clear()
                self._mode, self._band = "band", QRectF(pos, pos)
            else:
                if event.modifiers() & Qt.ShiftModifier:
                    self.sel ^= {key}
                elif key not in self.sel:
                    self.sel = {key}
                self._mode = "move"
                self._snap = self.board.to_dict()
                self._drag_items = self.board.expand(self.sel)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self._cursor = pos
        w = self.to_world(pos)
        if self._mode == "pan":
            self.pan += pos - self._last
            self._last = pos
        elif self._mode == "draw":
            last = self._stroke[-1]
            if math.hypot(w[0] - last[0], w[1] - last[1]) * self.zoom >= 1.2:
                self._stroke.append(w)
            start = self.board.shape_at(self._stroke[0], sketch.SNAP / self.zoom)
            now = self.board.shape_at(w, sketch.SNAP / self.zoom)
            self.hot = {("shape", s.id) for s in (start, now) if s is not None}
        elif self._mode == "erase":
            self._erase(w)
        elif self._mode == "move":
            dx, dy = (pos.x() - self._last.x()) / self.zoom, (pos.y() - self._last.y()) / self.zoom
            if abs(pos.x() - self._press.x()) + abs(pos.y() - self._press.y()) > 2:
                self._moved = True
            if self._moved:
                self.board.move(self._drag_items, dx, dy)
                self._last = pos
        elif self._mode == "end" and self._drag_end:
            self._moved = True
            self.board.move_end(self._drag_end[0], self._drag_end[1], w)
            s = self.board.shape_at(w, 8 / self.zoom)
            self.hot = {("shape", s.id)} if s else set()
        elif self._mode == "band" and self._band is not None:
            self._band = QRectF(self._press, pos).normalized()
        elif self.tool == "select":
            key = self.board.hit(w, 8 / self.zoom)
            self.setCursor(Qt.SizeAllCursor if key or self._handle_at(pos) else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        mode, self._mode = self._mode, ""
        if mode == "pan":
            self.set_tool(self.tool)
        elif mode == "draw":
            pts = self._stroke
            self._stroke = []
            self.hot = set()
            moved = sketch.path_len(pts) * self.zoom if len(pts) > 1 else 0
            if moved >= 3:
                self.checkpoint()
                res = self.board.add_stroke(pts)
                kind = {"shape": "shape", "retrace": "shape", "hand": "shape", "edge": "edge", "arrowhead": "edge",
                        "edge_hand": "edge", "ink": "ink"}.get(res.get("kind"), "")
                if kind:
                    self._flash((kind, res["id"]))
                self._changed()
                s = self.board.shapes.get(res.get("id")) if res.get("kind") == "shape" else None
                if s is not None and not s.label and not s.hand:
                    self.edit_label("shape", s.id)          # подпись — сразу с клавиатуры (или пишите от руки)
        elif mode == "erase":
            if self._snap is not None and self._snap != self.board.to_dict():
                self.checkpoint(self._snap)
            self._snap = None
            self._changed()
        elif mode in ("move", "end"):
            if self._moved:
                self.board.bind_free_ends()
                self.checkpoint(self._snap)
                self._changed()
            self._snap, self._drag_end, self.hot = None, None, set()
        elif mode == "band" and self._band is not None:
            a = self.to_world(self._band.topLeft())
            z = self.to_world(self._band.bottomRight())
            r = QRectF(QPointF(*a), QPointF(*z))
            b = self.board
            for s in b.shapes.values():
                if r.contains(QRectF(s.x, s.y, s.w, s.h)):
                    self.sel.add(("shape", s.id))
            for k in b.ink.values():
                if all(r.contains(QPointF(*p)) for p in k.pts):
                    self.sel.add(("ink", k.id))
            for n in b.notes.values():
                if r.contains(QPointF(n.x, n.y)):
                    self.sel.add(("note", n.id))
            for e in b.edges.values():
                if all(r.contains(QPointF(*p)) for p in (b.endpoint(e, "a"), b.endpoint(e, "b"))):
                    self.sel.add(("edge", e.id))
            self._band = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        w = self.to_world(event.position())
        key = self.board.hit(w, 8 / self.zoom)
        if key and key[0] in ("shape", "edge", "note"):
            self.edit_label(*key)
        elif key is None and self.tool in ("pen", "select"):
            self.checkpoint()
            sid = self.board.add_shape("rect", w[0] - 70, w[1] - 26, 140, 52)
            self._flash(("shape", sid))
            self._changed()
            self.edit_label("shape", sid)

    def wheelEvent(self, event) -> None:  # noqa: N802
        d = event.angleDelta()
        if event.modifiers() & Qt.ControlModifier:
            pos = event.position()
            w = self.to_world(pos)
            self.zoom = max(0.3, min(3.0, self.zoom * (1.0015 ** d.y())))
            self.pan = QPointF(pos.x() - w[0] * self.zoom, pos.y() - w[1] * self.zoom)
        elif event.modifiers() & Qt.ShiftModifier:
            self.pan += QPointF(d.y() / 2, 0)
        else:
            self.pan += QPointF(d.x() / 2, d.y() / 2)
        self.commit_edit()
        self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._cursor = QPointF(-100, -100)
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Space and not event.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if Qt.Key_1 <= key <= Qt.Key_4 and not event.modifiers():
            self.set_tool(TOOLS[key - Qt.Key_1])
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel:
            self.checkpoint()
            for kind, i in sorted(self.sel):
                self.board.delete(kind, i)
            self.sel.clear()
            self._changed()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and len(self.sel) == 1:
            kind, i = next(iter(self.sel))
            if kind in ("shape", "edge", "note"):
                self.edit_label(kind, i)
                return
        if key == Qt.Key_Escape and self.sel:
            self.sel.clear()
            self.update()
            return
        if key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.set_tool("select")
            b = self.board
            self.sel = ({("shape", i) for i in b.shapes} | {("edge", i) for i in b.edges}
                        | {("note", i) for i in b.notes} | {("ink", i) for i in b.ink})
            self.update()
            return
        if key == Qt.Key_0 and event.modifiers() & Qt.ControlModifier:
            self.fit()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space = False
            self.set_tool(self.tool)
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------- действия
    def _erase(self, w) -> None:
        r = 10 / self.zoom
        last = self._erase_last
        pts = [w]
        if last is not None and self._mode == "erase":
            n = int(math.hypot(w[0] - last[0], w[1] - last[1]) / (r / 2))
            pts = [(last[0] + (w[0] - last[0]) * k / (n + 1), last[1] + (w[1] - last[1]) * k / (n + 1))
                   for k in range(1, n + 2)]
        self._erase_last = w
        for p in pts:
            if self.board.erase(p, r):
                self.changed.emit()

    def _text_at(self, w) -> None:
        key = self.board.hit(w, 10 / self.zoom)
        if key and key[0] in ("shape", "edge", "note"):
            self.edit_label(*key)
            return
        self.checkpoint()
        nid = self.board.add_note(w[0], w[1] - 10, "")
        self.edit_label("note", nid)

    def _menu(self, event) -> None:
        w = self.to_world(event.position())
        key = self.board.hit(w, 8 / self.zoom)
        menu = QMenu(self)
        b = self.board

        def act(fn):
            def run():
                self.checkpoint()
                fn()
                self._changed()
            return run
        if key is None:
            menu.addAction("Новый узел здесь", lambda: self._new_node(w))
            if not b.is_empty():
                menu.addAction("Показать всю схему", self.fit)
        elif key[0] == "shape":
            s = b.shapes[key[1]]
            menu.addAction("Подписать…", lambda: self.edit_label("shape", s.id))
            forms = menu.addMenu("Форма")
            for kind in ("rect", "round", "ellipse", "circle", "diamond"):
                a = forms.addAction(sketch.KIND_NAMES[kind], act(lambda k=kind: setattr(s, "kind", k)))
                a.setCheckable(True)
                a.setChecked(s.kind == kind)
            if s.hand:
                menu.addAction("Подпись от руки — это просто рисунок", act(lambda: b.hand_to_ink(s.id)))
            menu.addSeparator()
            menu.addAction("Удалить", act(lambda: b.delete("shape", s.id)))
        elif key[0] == "edge":
            e = b.edges[key[1]]
            menu.addAction("Подписать…", lambda: self.edit_label("edge", e.id))
            way = menu.addMenu("Направление")

            def heads(ha, hb):
                e.head_a, e.head_b, e.explicit_b = ha, hb, True
            way.addAction("→  туда, как рисовали", act(lambda: heads(False, True)))
            way.addAction("←  обратно", act(lambda: b.reverse(e.id)))
            way.addAction("↔  в обе стороны", act(lambda: heads(True, True)))
            way.addAction("—  без стрелок", act(lambda: heads(False, False)))
            line = menu.addMenu("Линия")
            for style, name in (("solid", "сплошная"), ("dotted", "пунктир"), ("thick", "жирная")):
                a = line.addAction(name, act(lambda st=style: setattr(e, "style", st)))
                a.setCheckable(True)
                a.setChecked(e.style == style)
            menu.addAction("Это не стрелка, а рисунок", act(lambda: b.edge_to_ink(e.id)))
            menu.addSeparator()
            menu.addAction("Удалить", act(lambda: b.delete("edge", e.id)))
        elif key[0] == "ink":
            menu.addAction("Это фигура (узел)", act(lambda: b.ink_to_shape(key[1])))
            menu.addAction("Удалить", act(lambda: b.delete("ink", key[1])))
        elif key[0] == "note":
            menu.addAction("Изменить…", lambda: self.edit_label("note", key[1]))
            menu.addAction("Удалить", act(lambda: b.delete("note", key[1])))
        menu.exec(event.globalPosition().toPoint())

    def _new_node(self, w) -> None:
        self.checkpoint()
        sid = self.board.add_shape("rect", w[0] - 70, w[1] - 26, 140, 52)
        self._changed()
        self.edit_label("shape", sid)

    # ------------------------------------------------------------- рисование
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        col = colors()
        p.fillRect(self.rect(), col["bg"])
        p.setRenderHint(QPainter.Antialiasing)
        # точечная сетка — чтобы рука держала линию
        step = 24 * self.zoom
        if step >= 9:
            p.setPen(QPen(col["grid"], 1.6, Qt.SolidLine, Qt.RoundCap))
            x0 = self.pan.x() % step
            y0 = self.pan.y() % step
            y = y0
            while y < self.height():
                x = x0
                while x < self.width():
                    p.drawPoint(QPointF(x, y))
                    x += step
                y += step
        p.save()
        p.translate(self.pan)
        p.scale(self.zoom, self.zoom)
        now = time.time()
        flash = {k: max(0.0, 1 - (now - t) / 0.7) for k, t in self.flash.items()}
        paint_board(p, self.board, col, self.sel if self.tool == "select" else set(), self.hot, flash)
        if len(self._stroke) > 1:
            p.setPen(QPen(col["ink"], 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(smooth_path(self._stroke))
        p.restore()
        if self._band is not None:
            p.setPen(QPen(col["accent"], 1, Qt.DashLine))
            p.setBrush(_alpha(col["accent"], 25))
            p.drawRect(self._band)
        if self.tool == "eraser" and self._cursor.x() >= 0:
            p.setPen(QPen(col["muted"], 1.2))
            p.setBrush(_alpha(col["bg"], 120))
            p.drawEllipse(self._cursor, 10, 10)
        if self.board.is_empty() and not self._stroke:
            p.setPen(col["muted"])
            p.setFont(_font(10.4))
            p.drawText(self.rect().adjusted(30, 30, -30, -30), Qt.AlignCenter | Qt.TextWordWrap, HINT)
        p.end()


# ================================================================ панель доски
class BoardPanel(QFrame):
    """Доска с инструментами, живым Mermaid и кнопкой «Отправить схему»."""

    send = Signal(dict, str, object)       # доска, Mermaid, картинка PNG (bytes) или None
    closed = Signal()
    wide_toggled = Signal(bool)
    changed = Signal()

    def __init__(self, send_label: str = "Отправить схему", mermaid_title: str = "Что получит Claude"):
        super().__init__()
        self.setObjectName("BoardPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        self.task = QLabel("")
        self.task.setObjectName("BoardTask")
        self.task.setWordWrap(True)
        self.task.hide()
        lay.addWidget(self.task)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tools = W.Segmented(list(TOOL_LABELS))
        for b, tip in zip(self.tools.buttons, TOOL_TIPS):
            b.setToolTip(tip)
        self.tools.changed.connect(lambda i: self.canvas.set_tool(TOOLS[i]))
        bar.addWidget(self.tools)
        bar.addSpacing(6)
        self.undo_btn = self._icon("↶", "Отменить — Ctrl+Z", lambda: self.canvas.undo())
        self.redo_btn = self._icon("↷", "Вернуть — Ctrl+Shift+Z", lambda: self.canvas.redo())
        bar.addWidget(self.undo_btn)
        bar.addWidget(self.redo_btn)
        self.clear_btn = self._icon("Очистить", "Стереть всю доску (можно отменить)", lambda: self.canvas.clear())
        bar.addWidget(self.clear_btn)
        bar.addStretch(1)
        self.fit_btn = self._icon("Вписать", "Показать всю схему — Ctrl+0", lambda: self.canvas.fit())
        bar.addWidget(self.fit_btn)
        self.wide_btn = self._icon("Во всё окно", "Доска во всё окно (чат спрячется)", self._toggle_wide)
        self.wide_btn.setCheckable(True)
        bar.addWidget(self.wide_btn)
        self.close_btn = self._icon("×", "Свернуть доску — рисунок сохранится (Ctrl+D)", self.closed.emit)
        bar.addWidget(self.close_btn)
        lay.addLayout(bar)
        self.canvas = BoardCanvas()
        self.canvas.changed.connect(self._on_change)
        self.canvas.tool_changed.connect(self._sync_tool)
        lay.addWidget(self.canvas, 1)
        head = QHBoxLayout()
        self._mermaid_title = mermaid_title
        self.mermaid_btn = QPushButton(mermaid_title + "  ▾")
        self.mermaid_btn.setObjectName("Link")
        self.mermaid_btn.setCursor(Qt.PointingHandCursor)
        self.mermaid_btn.clicked.connect(self._toggle_mermaid)
        head.addWidget(self.mermaid_btn)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Meta")
        self.status.setWordWrap(True)
        lay.addLayout(head)
        self.mermaid = QPlainTextEdit()
        self.mermaid.setObjectName("BoardMermaid")
        self.mermaid.setReadOnly(True)
        self.mermaid.setFixedHeight(118)
        lay.addWidget(self.mermaid)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        foot.addWidget(self.status, 1)
        self.image_box = QCheckBox("Картинка")
        self.image_box.setToolTip("Приложить картинку доски — так Claude прочитает подписи и рисунок от руки. "
                                  "Включается сама, когда они есть")
        self.image_box.toggled.connect(self._image_toggled)
        foot.addWidget(self.image_box)
        self._send_label = send_label
        self.send_btn = QPushButton(send_label)
        self.send_btn.setObjectName("Primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("Ctrl+Enter. Комментарий и уверенность — в поле ответа под чатом")
        self.send_btn.clicked.connect(self.submit)
        foot.addWidget(self.send_btn, 0, Qt.AlignBottom)
        lay.addLayout(foot)
        self._image_manual: bool | None = None
        self._busy = False
        self._mermaid_timer = QTimer(self)
        self._mermaid_timer.setSingleShot(True)
        self._mermaid_timer.setInterval(80)
        self._mermaid_timer.timeout.connect(self._refresh)
        # каждое сочетание — один раз: два одинаковых Qt считает неоднозначными и не срабатывает ни одно
        keys: dict[str, object] = {}
        for std, extra, fn in ((QKeySequence.Undo, ["Ctrl+Z"], self.canvas.undo),
                               (QKeySequence.Redo, ["Ctrl+Shift+Z", "Ctrl+Y"], self.canvas.redo),
                               (None, ["Ctrl+Return", "Ctrl+Enter"], self.submit)):
            seqs = [k.toString() for k in QKeySequence.keyBindings(std)] if std is not None else []
            for text in seqs + extra:
                keys.setdefault(text, fn)
        for text, fn in keys.items():
            sc = QShortcut(QKeySequence(text), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._refresh()

    def _icon(self, text: str, tip: str, fn) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("Icon")
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    # ------------------------------------------------------------- состояние
    @property
    def board(self) -> Board:
        return self.canvas.board

    def load(self, data: dict | Board | None, fit: bool = True, keep_history: bool = False) -> None:
        board = data if isinstance(data, Board) else Board.from_dict(data)
        self._image_manual = None
        self.canvas.set_board(board, fit=fit, keep_history=keep_history)

    def set_task(self, text: str) -> None:
        self.task.setText(text)
        self.task.setVisible(bool(text))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh()

    def image_wanted(self) -> bool:
        return self.image_box.isChecked()

    def _sync_tool(self, tool: str) -> None:
        self.tools.buttons[TOOLS.index(tool)].setChecked(True)

    def _toggle_wide(self) -> None:
        self.wide_toggled.emit(self.wide_btn.isChecked())

    def _toggle_mermaid(self) -> None:
        show = not self.mermaid.isVisible()
        self.mermaid.setVisible(show)
        self.mermaid_btn.setText(self._mermaid_title + "  " + ("▾" if show else "▸"))

    def _image_toggled(self, on: bool) -> None:
        if self.image_box.signalsBlocked():
            return
        self._image_manual = on
        self._refresh()

    def _on_change(self) -> None:
        self._mermaid_timer.start()
        self.changed.emit()

    def _refresh(self) -> None:
        b = self.canvas.board
        need = b.needs_image()
        want = need if self._image_manual is None else self._image_manual
        self.image_box.blockSignals(True)
        self.image_box.setChecked(want)
        self.image_box.blockSignals(False)
        self.image_box.setVisible(need or self._image_manual is True)
        text = sketch.to_mermaid(b, want) if not b.is_empty() else "Нарисуйте схему — здесь появится её текст."
        if self.mermaid.toPlainText() != text:
            self.mermaid.setPlainText(text)
        self.status.setText(describe_status(b, want))
        self.send_btn.setEnabled(not b.is_empty() and not self._busy)
        self.send_btn.setText("Claude отвечает…" if self._busy else self._send_label)
        self.undo_btn.setEnabled(bool(self.canvas.undo_stack))
        self.redo_btn.setEnabled(bool(self.canvas.redo_stack))

    def submit(self) -> None:
        self.canvas.commit_edit()
        b = self.canvas.board
        if b.is_empty() or self._busy or not self.send_btn.isEnabled():
            return
        image = self.image_wanted()
        png = png_bytes(render_image(b)) if image else None
        self.send.emit(b.to_dict(), sketch.to_mermaid(b, image), png)


# ================================================================ схема для чтения (в чате и повторении)
class SchemaView(QWidget):
    """Готовая схема без правки: вписана по ширине, по щелчку — открыть на доске."""

    clicked = Signal()

    def __init__(self, board: Board, max_h: int = 420, clickable: bool = True):
        super().__init__()
        self.board = board
        self.max_h = max_h
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Открыть на доске — поправить или дорисовать")
        self._clickable = clickable
        self._bb = board.bounds() or (0, 0, 200, 100)

    def _scale(self, width: int) -> float:
        """По ширине колонки; высокую схему ужимаем по высоте, но не мельче 55 % — текст должен читаться."""
        bw = self._bb[2] - self._bb[0] + 40
        bh = self._bb[3] - self._bb[1] + 40
        k = min(1.0, (width - 2) / bw)
        if bh * k > self.max_h:
            k = max(min(k, 0.55), self.max_h / bh)
        return max(0.3, k)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return int((self._bb[3] - self._bb[1] + 40) * self._scale(width)) + 2

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(560, self.heightForWidth(560))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        h = self.heightForWidth(self.width())
        if self.height() != h:
            self.setFixedHeight(h)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = colors()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_qc(W.PALETTE.get("card_border", "rgba(0, 0, 0, 20)")), 1))
        p.setBrush(col["bg"])
        p.drawRoundedRect(r, 10, 10)
        k = self._scale(self.width())
        bw = (self._bb[2] - self._bb[0] + 40) * k
        p.translate((self.width() - bw) / 2, 0)
        p.scale(k, k)
        p.translate(20 - self._bb[0], 20 - self._bb[1])
        paint_board(p, self.board, col, handles=False)
        p.end()
