"""Внешний вид «Сводки» поверх темы Aqua: стили ленты и Читалки, иконка, значки разделов, шрифты."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QLinearGradient, QPainter, QPen, QPixmap

from . import theme

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
READ_FONT = "Literata"
READER_W = 680
CONTENT_MAX_W = 760
SIDEBAR_W = 216
# Размер текста статьи (pt) и межстрочный (процент от собственного интервала шрифта:
# у Literata он уже большой, 118% ≈ 1,6 от кегля — проверено на макете).
TEXT_SIZES = {"s": 11.6, "m": 12.8, "l": 14.2}
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
#FeedTitle {{ font-size: 12.4pt; font-weight: 650; letter-spacing: -0.15px; }}
#Meta {{ color: {c['muted']}; font-size: 8.8pt; }}
#MetaTopic {{ color: {c['accent']}; font-size: 8.8pt; font-weight: 600; }}
#ReaderMeta {{ color: {c['muted']}; font-size: 8.3pt; font-weight: 600; letter-spacing: 0.9px; }}
#ReaderTitle {{ font-size: 23pt; font-weight: 700; letter-spacing: -0.6px; }}
#ReaderH2 {{ font-size: 13.5pt; font-weight: 650; letter-spacing: -0.2px; }}
#ReaderH3 {{ font-size: 12pt; font-weight: 650; }}
#Body {{ font-family: "{READ_FONT}"; font-size: {size}pt; color: {reading_color(c)}; }}
#BodyFaded {{ font-family: "{READ_FONT}"; font-size: {size}pt; color: {c['faint']}; }}
#BodyOrig {{ font-family: "{READ_FONT}"; font-size: {size - 1.2:.1f}pt; color: {c['muted']}; }}
#QuoteText {{ font-family: "{READ_FONT}"; font-size: {size}pt; font-style: italic; color: {c['text_soft']}; }}
#Quote {{ border-left: 3px solid {c['accent']}; }}
#Code {{ font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 9.5pt; background: {c['card']};
         border: 1px solid {c['card_border']}; border-radius: 8px; padding: 10px 12px; color: {c['text_soft']}; }}
#Caption {{ color: {c['muted']}; font-size: 8.8pt; }}
#TopBar {{ background: {c['window_solid']}; border-bottom: 1px solid {c['hairline']}; }}
#Pill {{ background: {c['button']}; border: none; border-radius: 15px; padding: 7px 13px; font-size: 9.6pt; }}
#Pill:hover {{ background: {c['pressed']}; }}
#Pill:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
#Star {{ color: {c['faint']}; font-size: 15pt; background: transparent; border: none; padding: 0 2px; }}
#Star:checked {{ color: {c['warn']}; }}
#Star:hover {{ color: {c['warn']}; }}
#Original {{ color: {c['muted']}; font-size: 8.4pt; background: transparent; border: none; padding: 2px 4px; }}
#Original:hover {{ color: {c['accent']}; }}
#SearchBox {{ background: {c['input']}; border: 1px solid {c['input_border']}; border-radius: 12px;
              padding: 11px 16px; font-size: 13pt; }}
#SearchBox:focus {{ border: 1px solid {c['accent']}; }}
#Hint {{ color: {c['muted']}; font-size: 9pt; }}
#Answer {{ background: {c['card']}; border: 1px solid {c['card_border']}; border-radius: 12px; padding: 12px 14px; }}
#Dialog {{ background: {c['window_solid']}; }}
#DialogTitle {{ font-size: 15pt; font-weight: 700; letter-spacing: -0.3px; }}
#CodeBox {{ font-family: "JetBrains Mono", "Ubuntu Mono", monospace; font-size: 9.5pt; background: {c['card']};
            border: 1px solid {c['card_border']}; border-radius: 8px; padding: 4px 8px; }}
#Toast {{ background: {c['card_solid']}; border: 1px solid {c['card_border']}; border-radius: 12px; }}
#ToastText {{ color: {c['text']}; }}
QProgressBar#ReadProgress {{ background: transparent; border: none; }}
QProgressBar#ReadProgress::chunk {{ background: {c['accent']}; border-radius: 0; }}
"""


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """Иконка «Сводки»: тёмно-синий сквиркл (как у Aqua), три строки ленты и акцентная точка."""
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
    p.setPen(QPen(QColor("#E9EDF4"), s * 0.07, Qt.SolidLine, Qt.RoundCap))
    for y, x2 in ((0.36, 0.72), (0.5, 0.64), (0.64, 0.56)):
        p.drawLine(QPointF(s * 0.3, s * y), QPointF(s * x2, s * y))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawEllipse(QPointF(s * 0.72, s * 0.64), s * 0.07, s * 0.07)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


def nav_icon(name: str, color: str, size: int = 18) -> QIcon:
    if name == "settings":
        return theme.nav_icon("settings", color, size)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    s = size
    if name == "feed":
        for y, x2 in ((0.3, 0.8), (0.5, 0.8), (0.7, 0.58)):
            p.drawLine(QPointF(s * 0.2, s * y), QPointF(s * x2, s * y))
    elif name == "search":
        p.drawEllipse(QPointF(s * 0.44, s * 0.44), s * 0.24, s * 0.24)
        p.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.8, s * 0.8))
    elif name == "saved":
        r = QRectF(s * 0.3, s * 0.18, s * 0.4, s * 0.64)
        p.drawLine(r.topLeft(), r.topRight())
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.drawLine(r.topRight(), r.bottomRight())
        p.drawLine(r.bottomLeft(), QPointF(r.center().x(), r.bottom() - s * 0.16))
        p.drawLine(r.bottomRight(), QPointF(r.center().x(), r.bottom() - s * 0.16))
    p.end()
    return QIcon(pm)
