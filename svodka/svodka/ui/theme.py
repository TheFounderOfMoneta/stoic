"""Оформление (перенесено из Aqua Linux): палитра Graphite Blue (под WhiteSur/macOS), шрифты, рисованные иконки."""
from __future__ import annotations

import math
import shutil
import subprocess

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)

ACCENT = "#3A8DFF"

# Тёмная графитово-синяя тема — основная (как у WhiteSur Graphite-Blue).
DARK = {
    "name": "dark",
    "window": "rgba(19, 22, 29, 248)",       # фон контента
    "window_solid": "#13161D",
    "sidebar": "rgba(24, 28, 38, 214)",      # «стекло» сайдбара
    "sidebar_solid": "#181C26",
    "card": "rgba(255, 255, 255, 9)",
    "card_solid": "#1A1E27",
    "card_border": "rgba(255, 255, 255, 15)",
    "hairline": "rgba(255, 255, 255, 13)",
    "text": "#E9EDF4",
    "text_soft": "#C3CAD6",
    "muted": "#828C9E",
    "faint": "#5B6475",
    "accent": ACCENT,
    "accent_hover": "#5AA1FF",
    "accent_soft": "rgba(58, 141, 255, 38)",
    "accent_text": "#FFFFFF",
    "hover": "rgba(255, 255, 255, 13)",
    "pressed": "rgba(255, 255, 255, 20)",
    "selected": "rgba(255, 255, 255, 20)",
    "input": "rgba(255, 255, 255, 10)",
    "input_border": "rgba(255, 255, 255, 18)",
    "button": "rgba(255, 255, 255, 18)",
    "key": "rgba(255, 255, 255, 16)",
    "key_border": "rgba(255, 255, 255, 26)",
    "danger": "#FF5F57",
    "success": "#32D583",
    "warn": "#FFB547",
    "switch_off": "#3A404D",
    "shadow": QColor(0, 0, 0, 150),
    "scroll": "rgba(255, 255, 255, 40)",
}
LIGHT = {
    "name": "light",
    "window": "rgba(250, 251, 253, 250)",
    "window_solid": "#FAFBFD",
    "sidebar": "rgba(236, 239, 245, 225)",
    "sidebar_solid": "#ECEFF5",
    "card": "rgba(255, 255, 255, 235)",
    "card_solid": "#FFFFFF",
    "card_border": "rgba(15, 23, 42, 22)",
    "hairline": "rgba(15, 23, 42, 18)",
    "text": "#141821",
    "text_soft": "#353C4A",
    "muted": "#6B7385",
    "faint": "#9AA2B1",
    "accent": "#1F7BFF",
    "accent_hover": "#3D8DFF",
    "accent_soft": "rgba(31, 123, 255, 30)",
    "accent_text": "#FFFFFF",
    "hover": "rgba(15, 23, 42, 12)",
    "pressed": "rgba(15, 23, 42, 20)",
    "selected": "rgba(15, 23, 42, 16)",
    "input": "rgba(255, 255, 255, 255)",
    "input_border": "rgba(15, 23, 42, 28)",
    "button": "rgba(15, 23, 42, 14)",
    "key": "rgba(255, 255, 255, 255)",
    "key_border": "rgba(15, 23, 42, 40)",
    "danger": "#E5484D",
    "success": "#1F9D63",
    "warn": "#C77700",
    "switch_off": "#C9CED8",
    "shadow": QColor(15, 23, 42, 70),
    "scroll": "rgba(15, 23, 42, 50)",
}

_FONT_FAMILY = None


def ui_font_family() -> str:
    """SF Pro / Inter, если установлены (как в macOS), иначе системный (Ubuntu Sans)."""
    global _FONT_FAMILY
    if _FONT_FAMILY is None:
        families = set(QFontDatabase.families())
        for name in ("SF Pro Text", "SF Pro Display", "SF Pro", "Inter Variable", "Inter",
                     "Ubuntu Sans", "Ubuntu", "Cantarell"):
            if name in families:
                _FONT_FAMILY = name
                break
        else:
            _FONT_FAMILY = QGuiApplication.font().family()
    return _FONT_FAMILY


def apply_app_font(app) -> None:
    font = QFont(ui_font_family())
    font.setPointSizeF(10.0)
    font.setHintingPreference(QFont.PreferNoHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)


def is_dark() -> bool:
    if shutil.which("gsettings"):
        try:
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                                 capture_output=True, text=True, timeout=1).stdout
            if out.strip():
                return "light" not in out or "dark" in out
        except Exception:  # noqa: BLE001
            pass
    try:
        return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
    except AttributeError:
        return True


def palette(mode: str = "auto") -> dict:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if is_dark() else LIGHT


def _asset(name: str, color: str) -> str:
    """Маленькие PNG для QSS (стрелка списка, галочка) — QSS не умеет рисовать их сам."""
    from ..config import CACHE_DIR
    path = CACHE_DIR / "ui" / f"{name}-{color.strip('#')}.png"
    if path.exists():
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == "chevron":
        p.drawLine(QPointF(9, 12), QPointF(16, 19))
        p.drawLine(QPointF(16, 19), QPointF(23, 12))
    elif name == "check":
        p.drawLine(QPointF(8, 16.5), QPointF(13.5, 22))
        p.drawLine(QPointF(13.5, 22), QPointF(24, 10))
    p.end()
    pm.save(str(path))
    return str(path)


def stylesheet(c: dict) -> str:
    family = ui_font_family()
    chevron = _asset("chevron", c["muted"])
    check = _asset("check", "#FFFFFF")
    return f"""
* {{ font-family: "{family}"; }}
QWidget {{ color: {c['text']}; font-size: 10pt; background: transparent; }}
QLabel {{ background: transparent; }}
#PageTitle {{ font-size: 21pt; font-weight: 700; letter-spacing: -0.4px; }}
#PageSubtitle {{ color: {c['muted']}; font-size: 10pt; }}
#Hero {{ font-size: 26pt; font-weight: 700; letter-spacing: -0.6px; }}
#SectionLabel {{ color: {c['muted']}; font-size: 8.5pt; font-weight: 600; letter-spacing: 0.6px; }}
#Card {{ background: {c['card']}; border: 1px solid {c['card_border']}; border-radius: 14px; }}
#CardTitle {{ font-size: 11pt; font-weight: 650; }}
#Muted, QLabel[muted="true"] {{ color: {c['muted']}; }}
#Soft {{ color: {c['text_soft']}; }}
#StatValue {{ font-size: 22pt; font-weight: 700; letter-spacing: -0.5px; }}
#StatLabel {{ color: {c['muted']}; font-size: 8.8pt; }}
#Keycap {{
    background: {c['key']}; border: 1px solid {c['key_border']}; border-bottom-width: 2px;
    border-radius: 6px; padding: 2px 8px; font-size: 9.2pt; font-weight: 600; color: {c['text']};
}}
#Badge {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 9px; padding: 2px 9px;
          font-size: 8.5pt; font-weight: 600; }}
#StatusChip {{ background: {c['card']}; border: 1px solid {c['card_border']}; border-radius: 13px;
               padding: 5px 12px; font-size: 9pt; color: {c['text_soft']}; }}
#AppTitle {{ font-size: 12.5pt; font-weight: 700; letter-spacing: -0.2px; }}
#AppSubtitle {{ color: {c['muted']}; font-size: 8.5pt; }}
#SidebarFooter {{ color: {c['muted']}; font-size: 8.5pt; }}
QPushButton#Nav {{
    text-align: left; padding: 7px 10px; border: none; border-radius: 8px;
    background: transparent; font-size: 10.3pt; color: {c['text_soft']};
}}
QPushButton#Nav:hover {{ background: {c['hover']}; }}
QPushButton#Nav:checked {{ background: {c['selected']}; color: {c['text']}; font-weight: 600; }}
QPushButton {{
    background: {c['button']}; border: none; border-radius: 8px; padding: 6px 14px; color: {c['text']};
}}
QPushButton:hover {{ background: {c['pressed']}; }}
QPushButton:pressed {{ background: {c['hover']}; }}
QPushButton:disabled {{ color: {c['faint']}; }}
QPushButton:checked {{ background: {c['accent_soft']}; color: {c['accent']}; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_text']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#Danger {{ color: {c['danger']}; }}
QPushButton#Link {{ border: none; background: transparent; color: {c['accent']}; padding: 2px 4px; }}
QPushButton#Link:hover {{ color: {c['accent_hover']}; }}
QPushButton#Icon {{ border: none; background: transparent; padding: 3px 7px; border-radius: 7px; color: {c['muted']}; }}
QPushButton#Icon:hover {{ background: {c['hover']}; color: {c['text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {c['input']}; border: 1px solid {c['input_border']}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {c['accent']}; selection-color: white; color: {c['text']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {c['accent']};
}}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['card_solid']}; border: 1px solid {c['card_border']};
    border-radius: 8px; padding: 4px; selection-background-color: {c['accent']}; selection-color: white;
    outline: none; color: {c['text']}; }}
QTableWidget {{ background: transparent; border: 1px solid {c['card_border']}; border-radius: 10px;
    gridline-color: {c['hairline']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
    alternate-background-color: {c['card']}; }}
QTableWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid {c['hairline']};
    padding: 7px 8px; color: {c['muted']}; font-weight: 600; font-size: 8.8pt; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['input_border']};
    background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 36px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['switch_off']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: none; width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px; }}
QMenu {{ background: {c['card_solid']}; border: 1px solid {c['card_border']}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; color: {c['text']}; }}
QMenu::item:selected {{ background: {c['accent']}; color: white; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['hairline']}; margin: 4px 8px; }}
QToolTip {{ background: {c['card_solid']}; color: {c['text']}; border: 1px solid {c['card_border']};
    padding: 5px 9px; border-radius: 6px; }}
QMessageBox, QFileDialog, QDialog {{ background: {c['window_solid']}; }}
#Divider {{ background: {c['hairline']}; border: none; }}
QPushButton#SectionHeader {{ background: transparent; border: none; border-radius: 14px; padding: 0; text-align: left; }}
QPushButton#SectionHeader:hover {{ background: {c['hover']}; }}
QPushButton#SectionHeader:checked {{ border-bottom-left-radius: 0; border-bottom-right-radius: 0; }}
#SectionTitle {{ font-size: 10.5pt; font-weight: 600; }}
#Segmented {{ background: {c['button']}; border-radius: 9px; }}
QPushButton#Segment {{ background: transparent; border: none; border-radius: 7px; padding: 5px 18px;
    color: {c['text_soft']}; font-weight: 500; }}
QPushButton#Segment:checked {{ background: {c['card_solid']}; color: {c['text']}; font-weight: 600; }}
QPushButton#Help {{ background: {c['button']}; border: none; border-radius: 14px; padding: 0;
    color: {c['text_soft']}; font-weight: 700; font-size: 10.5pt; }}
QPushButton#Help:hover {{ background: {c['accent']}; color: white; }}
#CoachCard {{ background: {c['card_solid']}; border: 1px solid {c['card_border']}; border-radius: 16px; }}
#CoachTitle {{ font-size: 13pt; font-weight: 700; }}
#Welcome {{ background: {c['window_solid']}; }}
#WelcomeTitle {{ font-size: 26pt; font-weight: 750; letter-spacing: -0.6px; }}
#WelcomeText {{ font-size: 11.5pt; color: {c['text_soft']}; }}
#Step {{ font-size: 11pt; color: {c['text']}; }}
#StepNum {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 13px; font-weight: 700;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; }}
#Banner {{ background: {c['accent_soft']}; border-radius: 12px; padding: 10px 14px; color: {c['text']}; }}
#BannerError {{ background: rgba(255, 95, 87, 36); border-radius: 12px; padding: 10px 14px; color: {c['text']}; }}
#Big {{ font-size: 12pt; }}
QProgressBar {{ background: {c['switch_off']}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
"""


# ---------------------------------------------------------------------- иконки
def _draw_orb(p: QPainter, c: QPointF, r: float, accent: str = ACCENT, glow: bool = True) -> None:
    if glow:
        g = QRadialGradient(c, r * 2.0)
        g.setColorAt(0.0, QColor(58, 141, 255, 120))
        g.setColorAt(1.0, QColor(58, 141, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r * 2.0, r * 2.0)
    body = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 1.45)
    body.setColorAt(0.0, QColor("#F2FBFF"))
    body.setColorAt(0.28, QColor("#9FE0FF"))
    body.setColorAt(0.62, QColor(accent))
    body.setColorAt(1.0, QColor("#1238B8"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(body))
    p.drawEllipse(c, r, r)
    blob = QRadialGradient(QPointF(c.x() + r * 0.3, c.y() + r * 0.25), r * 0.7)
    blob.setColorAt(0.0, QColor(108, 242, 255, 140))
    blob.setColorAt(1.0, QColor(108, 242, 255, 0))
    p.setBrush(QBrush(blob))
    p.drawEllipse(c, r, r)
    p.setBrush(QColor(255, 255, 255, 185))
    p.drawEllipse(QPointF(c.x() - r * 0.38, c.y() - r * 0.48), r * 0.30, r * 0.18)


def orb_pixmap(size: int = 64, accent: str = ACCENT, ring: QColor | None = None) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(size / 2, size / 2)
    _draw_orb(p, c, size * 0.36, accent)
    if ring is not None:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ring, size * 0.07))
        p.drawEllipse(c, size * 0.44, size * 0.44)
    p.end()
    return pm


def squircle_path(rect: QRectF, n: float = 5.0) -> QPainterPath:
    """Суперэллипс — форма иконок macOS Big Sur / WhiteSur."""
    path = QPainterPath()
    cx, cy = rect.center().x(), rect.center().y()
    a, b = rect.width() / 2, rect.height() / 2
    steps = 96
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        ct, st = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(ct) ** (2 / n), ct)
        y = cy + b * math.copysign(abs(st) ** (2 / n), st)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """Иконка в стиле WhiteSur: тёмно-синий сквиркл со светящимся шаром и волной."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    rect = QRectF(s * 0.06, s * 0.05, s * 0.88, s * 0.88)
    shape = squircle_path(rect)
    # тень
    p.save()
    p.translate(0, s * 0.012)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawPath(squircle_path(rect.adjusted(-s * 0.005, 0, s * 0.005, s * 0.012)))
    p.restore()
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0.0, QColor("#2A3F66"))
    bg.setColorAt(0.55, QColor("#16223A"))
    bg.setColorAt(1.0, QColor("#0C1222"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(bg))
    p.drawPath(shape)
    p.save()
    p.setClipPath(shape)
    # мягкая «волна» как на обоях
    wave = QPainterPath()
    wave.moveTo(rect.left(), rect.top() + rect.height() * 0.78)
    wave.cubicTo(rect.left() + rect.width() * 0.35, rect.top() + rect.height() * 0.55,
                 rect.left() + rect.width() * 0.65, rect.top() + rect.height() * 0.95,
                 rect.right(), rect.top() + rect.height() * 0.62)
    wave.lineTo(rect.bottomRight())
    wave.lineTo(rect.bottomLeft())
    wave.closeSubpath()
    wg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    wg.setColorAt(0.0, QColor(58, 120, 200, 120))
    wg.setColorAt(1.0, QColor(30, 70, 140, 60))
    p.setBrush(QBrush(wg))
    p.drawPath(wave)
    # блик сверху
    hl = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.center().y()))
    hl.setColorAt(0.0, QColor(255, 255, 255, 46))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(hl))
    p.drawRect(rect)
    p.restore()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, s * 0.006)))
    p.drawPath(shape)
    _draw_orb(p, QPointF(s * 0.5, s * 0.46), s * 0.2)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    big = app_icon_pixmap(256)
    for s in (16, 22, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(big.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return icon


def tray_pixmap(state: str = "ready", size: int = 44) -> QPixmap:
    """Монохромный значок для верхней панели: пять столбиков звуковой волны."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = {"recording": QColor(ACCENT), "error": QColor("#FF5F57")}.get(state, QColor("#FFFFFF"))
    if state in ("loading", "downloading", "unloaded"):
        color.setAlpha(120)
    heights = (0.30, 0.62, 0.86, 0.62, 0.30)
    w = size * 0.09
    gap = size * 0.075
    total = len(heights) * w + (len(heights) - 1) * gap
    x = (size - total) / 2
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for h in heights:
        bh = size * h * 0.8
        p.drawRoundedRect(QRectF(x, (size - bh) / 2, w, bh), w / 2, w / 2)
        x += w + gap
    if state == "error":
        p.setBrush(QColor("#FF5F57"))
        p.drawEllipse(QPointF(size * 0.82, size * 0.2), size * 0.12, size * 0.12)
    p.end()
    return pm


def nav_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Тонкие линейные иконки (SF Symbols-подобные) для сайдбара."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = size
    if name == "home":
        # капля/шар — «Главная»
        p.drawEllipse(QPointF(s / 2, s / 2), s * .32, s * .32)
        p.drawArc(QRectF(s * .3, s * .28, s * .22, s * .22), 100 * 16, 110 * 16)
    elif name == "history":
        p.drawEllipse(QPointF(s / 2, s / 2), s * .33, s * .33)
        p.drawLine(QPointF(s / 2, s / 2), QPointF(s / 2, s * .31))
        p.drawLine(QPointF(s / 2, s / 2), QPointF(s * .64, s * .58))
    elif name == "dictionary":
        p.drawRoundedRect(QRectF(s * .25, s * .17, s * .5, s * .66), 2.5, 2.5)
        p.drawLine(QPointF(s * .36, s * .36), QPointF(s * .64, s * .36))
        p.drawLine(QPointF(s * .36, s * .5), QPointF(s * .56, s * .5))
    elif name == "replace":
        p.drawLine(QPointF(s * .22, s * .37), QPointF(s * .76, s * .37))
        p.drawLine(QPointF(s * .64, s * .26), QPointF(s * .76, s * .37))
        p.drawLine(QPointF(s * .78, s * .63), QPointF(s * .24, s * .63))
        p.drawLine(QPointF(s * .36, s * .74), QPointF(s * .24, s * .63))
    elif name == "instructions":
        path = QPainterPath()
        cx, cy = s * .5, s * .5
        for i in range(8):
            ang = math.pi / 4 * i - math.pi / 2
            r = s * .34 if i % 2 == 0 else s * .1
            pt = QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "settings":
        for k, y in enumerate((.3, .5, .7)):
            p.drawLine(QPointF(s * .2, s * y), QPointF(s * .8, s * y))
            x = (.38, .64, .46)[k]
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(s * x, s * y), s * .07, s * .07)
            p.setBrush(Qt.NoBrush)
    elif name == "model":
        p.drawRoundedRect(QRectF(s * .29, s * .29, s * .42, s * .42), 3, 3)
        for k in (.4, .6):
            p.drawLine(QPointF(s * k, s * .16), QPointF(s * k, s * .29))
            p.drawLine(QPointF(s * k, s * .71), QPointF(s * k, s * .84))
            p.drawLine(QPointF(s * .16, s * k), QPointF(s * .29, s * k))
            p.drawLine(QPointF(s * .71, s * k), QPointF(s * .84, s * k))
    p.end()
    return QIcon(pm)
