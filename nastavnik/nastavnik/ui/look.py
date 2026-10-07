"""Внешний вид «Наставника» поверх темы Aqua: стили чата и карточек, иконка, значки разделов, шрифты.

Типографика текста Claude — как в Читалке «Сводки»: колонка 680 px (~66 знаков в строке),
Literata, межстрочный ~1,6 от кегля, цвет чуть мягче основного.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from . import theme

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
READ_FONT = "Literata"
READER_W = 680
CONTENT_MAX_W = 760
SIDEBAR_W = 216
# Размер текста (pt) и межстрочный (процент от собственного интервала шрифта: у Literata он уже
# большой, 118 % ≈ 1,6 от кегля — проверено в «Сводке»).
TEXT_SIZES = {"s": 11.2, "m": 12.4, "l": 13.8}
LINE_HEIGHT = 118
_fonts_loaded = False


def load_fonts() -> None:
    global _fonts_loaded
    if _fonts_loaded:
        return
    for f in ("Literata.ttf", "Literata-Italic.ttf"):
        path = FONTS_DIR / f
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    _fonts_loaded = True


def reading_color(c: dict) -> str:
    """Чуть мягче основного текста — меньше устают глаза при долгом чтении."""
    return "#D5DCE7" if c["name"] == "dark" else "#1D222B"


def stylesheet(c: dict, text_size: str = "m") -> str:
    size = TEXT_SIZES.get(text_size, TEXT_SIZES["m"])
    return theme.stylesheet(c) + f"""
#Body {{ font-family: "{READ_FONT}"; font-size: {size}pt; color: {reading_color(c)}; }}
#BodyH {{ font-size: {size + 1.2:.1f}pt; font-weight: 650; letter-spacing: -0.2px; }}
#Code {{ font-family: "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", monospace; font-size: 9.6pt;
         background: {c['card']}; border: 1px solid {c['card_border']}; border-radius: 10px; padding: 10px 12px;
         color: {c['text_soft']}; }}
#CodeScroll {{ background: transparent; border: none; }}
#UserBubble {{ background: {c['accent_soft']}; border-radius: 14px; }}
#UserText {{ font-size: {size - 1.0:.1f}pt; color: {c['text']}; }}
#AppNote {{ color: {c['muted']}; font-size: 8.6pt; background: {c['card']}; border-radius: 10px; padding: 3px 10px; }}
#Thinking {{ color: {c['muted']}; font-size: 9pt; }}
#ChatInput {{ background: {c['input']}; border: 1px solid {c['input_border']}; border-radius: 12px;
              padding: 9px 12px; font-size: 10.6pt; }}
#ChatInput:focus {{ border: 1px solid {c['accent']}; }}
#TopBar {{ background: {c['window_solid']}; border-bottom: 1px solid {c['hairline']}; }}
#BottomBar {{ background: {c['window_solid']}; border-top: 1px solid {c['hairline']}; }}
#Meta {{ color: {c['muted']}; font-size: 8.8pt; }}
#StepChip {{ color: {c['faint']}; font-size: 8.4pt; font-weight: 600; letter-spacing: 0.4px; padding: 3px 8px;
             border-radius: 9px; }}
#StepChip[state="done"] {{ color: {c['muted']}; }}
#StepChip[state="now"] {{ color: {c['accent']}; background: {c['accent_soft']}; }}
#Pill {{ background: {c['button']}; border: none; border-radius: 15px; padding: 7px 13px; font-size: 9.6pt; }}
#Pill:hover {{ background: {c['pressed']}; }}
#Pill:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
#Star {{ color: {c['faint']}; font-size: 17pt; background: transparent; border: none; padding: 0 2px; }}
#Star:checked {{ color: {c['warn']}; }}
#Star:hover {{ color: {c['warn']}; }}
#Question {{ font-family: "{READ_FONT}"; font-size: {size + 3:.1f}pt; color: {c['text']}; }}
#Answer {{ font-family: "{READ_FONT}"; font-size: {size + 0.6:.1f}pt; color: {reading_color(c)}; }}
#GradeHint {{ color: {c['muted']}; font-size: 8.4pt; }}
#Loop {{ font-family: "{READ_FONT}"; font-size: {size + 1:.1f}pt; font-style: italic; color: {c['text']}; }}
#TopicTitle {{ font-size: 12.6pt; font-weight: 650; letter-spacing: -0.15px; }}
#Big {{ font-size: 12pt; }}
#Dialog {{ background: {c['window_solid']}; }}
#DialogTitle {{ font-size: 15pt; font-weight: 700; letter-spacing: -0.3px; }}
#BoardPanel {{ background: {c['window_solid']}; border-left: 1px solid {c['hairline']}; }}
#BoardTask {{ font-family: "{READ_FONT}"; font-size: {size - 1.4:.1f}pt; color: {c['text_soft']}; background: {c['card']};
              border-radius: 9px; padding: 7px 11px; }}
#BoardEditor {{ background: {c['card_solid']}; border: 1px solid {c['accent']}; border-radius: 7px; padding: 2px 7px;
                font-size: 10pt; color: {c['text']}; }}
#BoardMermaid {{ font-family: "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", monospace; font-size: 8.8pt;
                 background: {c['card']}; border: 1px solid {c['card_border']}; border-radius: 8px; padding: 4px 6px;
                 color: {c['text_soft']}; }}
#BoardChip {{ background: {c['button']}; border: none; border-radius: 13px; padding: 5px 12px; font-size: 9.2pt; }}
#BoardChip:hover {{ background: {c['pressed']}; }}
#BoardChip:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
#BoardChip[suggest="true"] {{ background: {c['accent']}; color: {c['accent_text']}; font-weight: 600; }}
#SessionSplit::handle {{ background: {c['hairline']}; }}
#Toast {{ background: {c['card_solid']}; border: 1px solid {c['card_border']}; border-radius: 12px; }}
#ToastText {{ color: {c['text']}; }}
#ErrorCard {{ background: rgba(255, 95, 87, 30); border-radius: 12px; }}
#MoodValue {{ font-size: 20pt; font-weight: 700; color: {c['accent']}; }}
#Chip {{ background: {c['button']}; border: none; border-radius: 13px; padding: 5px 11px; font-size: 9.2pt;
         color: {c['text_soft']}; }}
#Chip:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
#Chip:hover {{ background: {c['pressed']}; }}
QProgressBar#Thin {{ background: {c['switch_off']}; border: none; border-radius: 3px; }}
QProgressBar#Thin::chunk {{ background: {c['accent']}; border-radius: 3px; }}
"""


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """Иконка «Наставника»: тёмно-синий сквиркл (как у Aqua и «Сводки»), раскрытая книга и акцентная искра."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    rect = QRectF(s * 0.06, s * 0.05, s * 0.88, s * 0.88)
    shape = theme.squircle_path(rect)
    p.save()
    p.translate(0, s * 0.012)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawPath(theme.squircle_path(rect.adjusted(-s * 0.005, 0, s * 0.005, s * 0.012)))
    p.restore()
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0.0, QColor("#2A3F66"))
    bg.setColorAt(0.55, QColor("#16223A"))
    bg.setColorAt(1.0, QColor("#0C1222"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawPath(shape)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, s * 0.006)))
    p.drawPath(shape)
    # раскрытая книга: две страницы-дуги
    pen = QPen(QColor("#E9EDF4"), s * 0.055, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    for side in (-1, 1):
        path = QPainterPath()
        path.moveTo(s * 0.5, s * 0.70)
        path.cubicTo(s * (0.5 + side * 0.08), s * 0.62, s * (0.5 + side * 0.2), s * 0.62, s * (0.5 + side * 0.25),
                     s * 0.66)
        path.lineTo(s * (0.5 + side * 0.25), s * 0.42)
        path.cubicTo(s * (0.5 + side * 0.2), s * 0.38, s * (0.5 + side * 0.08), s * 0.38, s * 0.5, s * 0.46)
        p.drawPath(path)
    p.drawLine(QPointF(s * 0.5, s * 0.46), QPointF(s * 0.5, s * 0.70))
    # искра над книгой
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    star = QPainterPath()
    cx, cy, r = s * 0.5, s * 0.25, s * 0.085
    for i in range(8):
        ang = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.32
        pt = QPointF(cx + math.cos(ang) * rr, cy + math.sin(ang) * rr)
        star.moveTo(pt) if i == 0 else star.lineTo(pt)
    star.closeSubpath()
    p.drawPath(star)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


def nav_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Тонкие линейные иконки разделов (как SF Symbols)."""
    if name == "settings":
        return theme.nav_icon("settings", color, size)
    if name == "home":
        return theme.nav_icon("home", color, size)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    s = float(size)
    if name == "learn":                      # раскрытая книга
        for side in (-1, 1):
            path = QPainterPath()
            path.moveTo(s * 0.5, s * 0.78)
            path.lineTo(s * (0.5 + side * 0.32), s * 0.72)
            path.lineTo(s * (0.5 + side * 0.32), s * 0.24)
            path.lineTo(s * 0.5, s * 0.3)
            p.drawPath(path)
        p.drawLine(QPointF(s * 0.5, s * 0.3), QPointF(s * 0.5, s * 0.78))
    elif name == "review":                   # две карточки
        p.drawRoundedRect(QRectF(s * 0.18, s * 0.3, s * 0.48, s * 0.46), 2.5, 2.5)
        path = QPainterPath()
        path.moveTo(s * 0.32, s * 0.3)
        path.lineTo(s * 0.32, s * 0.2)
        path.lineTo(s * 0.8, s * 0.2)
        path.lineTo(s * 0.8, s * 0.62)
        path.lineTo(s * 0.66, s * 0.62)
        p.drawPath(path)
    elif name == "progress":                 # столбики
        for x, h in ((0.26, 0.3), (0.44, 0.48), (0.62, 0.62)):
            p.drawLine(QPointF(s * x, s * 0.8), QPointF(s * x, s * (0.8 - h)))
        p.drawLine(QPointF(s * 0.16, s * 0.8), QPointF(s * 0.84, s * 0.8))
    elif name == "talk":                     # облачко речи
        path = QPainterPath()
        path.addRoundedRect(QRectF(s * 0.16, s * 0.22, s * 0.68, s * 0.46), 6, 6)
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.34, s * 0.68), QPointF(s * 0.28, s * 0.82))
        p.drawLine(QPointF(s * 0.28, s * 0.82), QPointF(s * 0.46, s * 0.68))
    p.end()
    return QIcon(pm)
