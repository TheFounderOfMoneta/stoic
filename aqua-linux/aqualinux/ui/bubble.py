"""Плавающая панель (floating bar / pill) в стиле Aqua Voice.

Состояния:
  idle        — маленькая полупрозрачная капсула внизу экрана (видна, если включена);
  listening   — капсула раскрывается пружиной: синий шар + живая звуковая волна;
  hands_free  — то же + красная кнопка «стоп» справа (щелчок — остановить);
  processing  — волна схлопывается в точки, по ним бежит блик, шар вращается;
  loading     — модель ещё загружается;
  message     — короткое сообщение (ошибка / «речь не распознана»).
Дополнительно: панель живого текста над капсулой и чип Edit Mode («✎ 12 слов выделено»).

Окно — override-redirect (X11), не забирает фокус, всегда сверху. Мышь
принимается только внутри видимой капсулы (XShape input region), остальное
окно «прозрачно» для кликов.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, QPoint
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont, QFontMetricsF,
                           QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

WIN_W, WIN_H = 640, 210
EDGE = 14            # отступ капсулы от края окна (место под тень)


class Spring:
    """Пружина с небольшим перелётом — как у системных анимаций macOS."""

    def __init__(self, value: float, stiffness: float = 420.0, damping: float = 30.0):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.k = stiffness
        self.c = damping

    def step(self, dt: float) -> None:
        steps = max(1, int(dt / 0.004))
        h = dt / steps
        for _ in range(steps):
            force = -self.k * (self.value - self.target) - self.c * self.velocity
            self.velocity += force * h
            self.value += self.velocity * h

    def settled(self) -> bool:
        return abs(self.value - self.target) < 0.05 and abs(self.velocity) < 0.05

    def snap(self, value: float) -> None:
        self.value = self.target = float(value)
        self.velocity = 0.0


def approach(current: float, target: float, rate: float, dt: float) -> float:
    return target + (current - target) * math.exp(-rate * dt)


def rgba(hex_color: str, alpha: float) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


class Bubble(QWidget):
    clicked = Signal()              # щелчок по капсуле → длинная запись без удержания
    stop_clicked = Signal()         # красная кнопка
    cancel_clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal(int, int)        # новое смещение после перетаскивания

    def __init__(self, settings):
        super().__init__(None)
        self.settings = settings
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus | Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setFixedSize(WIN_W, WIN_H)
        self.setWindowTitle("Aqua Linux")

        self.state = "idle"
        self.mode_label = ""
        self.hint = "Удерживайте Правый Alt"
        self.message = ""
        self.message_kind = "info"
        self.live_text = ""
        self.chip_text = ""
        self.model_loading = False

        # Анимируемые величины
        self.w = Spring(40)
        self.h = Spring(8)
        self.orb = 0.0          # 0..1 видимость шара
        self.wave = 0.0         # 0..1 видимость волны
        self.stop = 0.0         # 0..1 видимость красной кнопки
        self.proc = 0.0         # 0..1 «обработка»
        self.msg = 0.0
        self.hover = 0.0
        self.text_alpha = 0.0
        self.chip_alpha = 0.0
        self.text_w = Spring(0, 300, 28)
        self.text_h = Spring(0, 300, 28)
        self.appear = 1.0       # общая прозрачность (для режима «не показывать в покое»)
        self.level = 0.0
        self.level_target = 0.0
        self.bars: deque = deque([0.0] * 26, maxlen=26)
        self.bar_shift = 0.0
        self._bar_accum = 0.0
        self._bar_peak = 0.0
        self.phase = 0.0
        self.pulse = 0.0
        self.success = 0.0

        self._hovered = False
        self._press_pos = None
        self._dragging = False
        self._drag_origin = None
        self._last = time.monotonic()
        self._msg_until = 0.0
        self.base_state = "idle"     # куда вернуться после сообщения (запись/обработка/покой)
        self._input_rect = None
        self._xshape = None

        self.font_text = QFont(self.font())
        self.font_text.setPixelSize(14)
        self.font_small = QFont(self.font())
        self.font_small.setPixelSize(12)
        self.font_small.setWeight(QFont.Medium)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(7)
        self.timer.timeout.connect(self._tick)
        self._base_interval = 7
        self.apply_targets()
        # Монитор отключили/подключили, сменился масштаб или док — облачко не должно остаться
        # за краем несуществующего экрана.
        app = QGuiApplication.instance()
        if app is not None:
            for signal in (app.screenAdded, app.screenRemoved, app.primaryScreenChanged):
                signal.connect(self._screens_changed)
            for screen in app.screens():
                screen.availableGeometryChanged.connect(self._screens_changed)

    # ------------------------------------------------------------ публичное API
    def set_state(self, state: str, mode_label: str = "") -> None:
        if state == self.state and mode_label == self.mode_label:
            return
        prev = self.state
        self.state = state
        self.mode_label = mode_label
        if state in ("listening", "hands_free") and prev not in ("listening", "hands_free"):
            self.bars.extend([0.0] * self.bars.maxlen)
            self.level = self.level_target = 0.0
        if state == "idle" and prev == "processing":
            self.success = 1.0
        if state != "message":
            self.message = ""
        self.apply_targets()
        self.reposition()
        self._kick()

    def show_message(self, text: str, kind: str = "info", ms: int = 2200) -> None:
        self.message = text
        self.message_kind = kind
        self.state = "message"
        self._msg_until = time.monotonic() + ms / 1000
        self.apply_targets()
        self.reposition()
        self._kick()

    def set_level(self, level: float) -> None:
        self.level_target = max(self.level_target * 0.6, float(level))
        self._bar_peak = max(self._bar_peak, float(level))

    def set_live_text(self, text: str) -> None:
        self.live_text = text or ""
        self.apply_targets()
        self._kick()

    def set_chip(self, text: str) -> None:
        self.chip_text = text or ""
        self.apply_targets()
        self._kick()

    def set_hint(self, text: str) -> None:
        self.hint = text

    def set_model_loading(self, loading: bool) -> None:
        self.model_loading = loading
        self._kick()

    def apply_targets(self) -> None:
        """Целевые размеры и видимость частей для текущего состояния."""
        glass = self.settings.get("bubble.style", "glass") == "glass"
        demo = self.settings.get("bubble.demo_mode", False)
        st = self.state
        if st == "idle":
            hovered = self._hovered
            self.w.target, self.h.target = (56, 14) if hovered else (40, 8)
        elif st in ("listening", "hands_free"):
            base = 168 if glass else 150
            if st == "hands_free":
                base += 30
            if demo:
                base += self._text_width("Диктовка с Aqua Linux", self.font_small) + 14
            self.w.target, self.h.target = base, 38
        elif st == "processing":
            self.w.target, self.h.target = (132, 38)
        elif st == "loading":
            self.w.target = 60 + self._text_width("Загружаю модель…", self.font_small)
            self.h.target = 38
        elif st == "message":
            self.w.target = 58 + self._text_width(self.message, self.font_small)
            self.h.target = 38
        self.w.target = min(self.w.target, WIN_W - 2 * EDGE)

    # -------------------------------------------------------------- геометрия
    def _text_width(self, text: str, font: QFont) -> float:
        return QFontMetricsF(font).horizontalAdvance(text)

    def _anchor_top(self) -> bool:
        return self.settings.get("bubble.position", "bottom") == "top"

    def pill_rect(self) -> QRectF:
        w, h = max(4.0, self.w.value), max(4.0, self.h.value)
        cx = WIN_W / 2
        cy = EDGE + 19 if self._anchor_top() else WIN_H - EDGE - 19
        return QRectF(cx - w / 2, cy - h / 2, w, h)

    def reposition(self) -> None:
        screen = None
        if self.settings.get("bubble.follow_mouse_screen", True):
            screen = QGuiApplication.screenAt(QCursor.pos())
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = self.settings.get("bubble.margin", "auto")
        if margin == "auto" or margin is None:
            from ..desktop import bubble_bottom_margin
            margin = bubble_bottom_margin() if not self._anchor_top() else 14
        margin = int(margin)
        ox = int(self.settings.get("bubble.offset_x", 0))
        oy = int(self.settings.get("bubble.offset_y", 0))
        x = geo.center().x() - WIN_W // 2 + ox
        if self._anchor_top():
            y = geo.top() + margin - EDGE + oy
        else:
            y = geo.bottom() - margin - WIN_H + EDGE + oy
        x = max(geo.left() - WIN_W // 3, min(x, geo.right() - WIN_W * 2 // 3))
        y = max(geo.top() - EDGE, min(y, geo.bottom() - WIN_H + EDGE))
        if self.pos() != QPoint(x, y):
            self.move(x, y)

    def _visible_wanted(self) -> bool:
        return self.settings.get("bubble.show", True) or self.state != "idle"

    # -------------------------------------------------------------- анимация
    def update_frame_rate(self) -> None:
        """Частота кадров по мощности и питанию: 144 Гц от сети, 60 от батареи, 30 в экономии."""
        try:
            from .. import perf
            self._base_interval = perf.bubble_interval_ms(self.settings)
            self.timer.setInterval(self._base_interval)
        except Exception:  # noqa: BLE001
            pass

    def _screens_changed(self, *_args) -> None:
        QTimer.singleShot(400, self._after_screens_changed)

    def _after_screens_changed(self) -> None:
        app = QGuiApplication.instance()
        if app is not None:
            for screen in app.screens():
                try:
                    screen.availableGeometryChanged.disconnect(self._screens_changed)
                except (RuntimeError, TypeError):
                    pass
                screen.availableGeometryChanged.connect(self._screens_changed)
        self._input_rect = None          # масштаб мог смениться — зону кликов пересчитать
        self.reposition()
        self._kick()

    def _kick(self) -> None:
        if self._visible_wanted() and not self.isVisible():
            self.reposition()
            self.show()
        if not self.timer.isActive():
            self._last = time.monotonic()
            self.timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last))
        self._last = now
        st = self.state

        if st == "message" and now >= self._msg_until:
            self.state = self.base_state if self.base_state in ("idle", "processing") else "idle"
            self.message = ""
            self.apply_targets()

        listening = st in ("listening", "hands_free")
        self.w.step(dt)
        self.h.step(dt)
        self.orb = approach(self.orb, 1.0 if st in ("listening", "hands_free", "processing", "loading") else 0.0, 14, dt)
        self.wave = approach(self.wave, 1.0 if listening else 0.0, 12, dt)
        self.stop = approach(self.stop, 1.0 if st == "hands_free" else 0.0, 14, dt)
        self.proc = approach(self.proc, 1.0 if st == "processing" else 0.0, 10, dt)
        self.msg = approach(self.msg, 1.0 if st == "message" else 0.0, 16, dt)
        self.hover = approach(self.hover, 1.0 if (self._hovered and st == "idle") else 0.0, 12, dt)
        self.success = approach(self.success, 0.0, 5, dt)
        show_text = bool(self.live_text) and st in ("listening", "hands_free", "processing")
        self.text_alpha = approach(self.text_alpha, 1.0 if show_text else 0.0, 12, dt)
        self.chip_alpha = approach(self.chip_alpha, 1.0 if self.chip_text and st != "idle" else 0.0, 12, dt)
        want = self._visible_wanted()
        self.appear = approach(self.appear, 1.0 if want else 0.0, 10, dt)

        # Уровень: быстрая атака, плавный спад.
        rate = 28 if self.level_target > self.level else 7
        self.level = approach(self.level, self.level_target, rate, dt)
        self.level_target = approach(self.level_target, 0.0, 6, dt)
        # Лента столбиков: новый столбик каждые 42 мс, плавный сдвиг.
        self._bar_accum += dt
        period = 0.042
        while self._bar_accum >= period:
            self._bar_accum -= period
            value = self._bar_peak if listening else 0.0
            self.bars.append(value)
            self._bar_peak = self.level * 0.5
        self.bar_shift = self._bar_accum / period
        speed = 2.6 if st == "processing" else (1.1 + self.level * 1.5)
        self.phase = (self.phase + dt * speed) % (math.tau * 1000)
        self.pulse = (self.pulse + dt) % 1000

        self._update_input_region()
        self.update()
        # Долгая загрузка модели (первый запуск, медленный интернет) — крутим значок
        # с частотой 30 Гц, а не 144: облачко не должно нагружать процессор, пока ждём.
        interval = 33 if (st == "idle" and self.model_loading) else self._base_interval
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)

        idle_settled = (st == "idle" and self.w.settled() and self.h.settled() and self.orb < 0.01
                        and self.msg < 0.01 and self.text_alpha < 0.01 and self.chip_alpha < 0.01
                        and abs(self.hover - (1.0 if self._hovered else 0.0)) < 0.01
                        and self.success < 0.01 and not self.model_loading)
        if idle_settled:
            if not want and self.appear < 0.02:
                self.hide()
                self.timer.stop()
            elif want and self.appear > 0.98:
                self.timer.stop()

    # -------------------------------------------------------- зона для мыши (XShape)
    def _interactive_rect(self) -> QRectF:
        rect = self.pill_rect()
        if self.state == "idle":
            return rect.adjusted(-12, -10, 12, 8)
        return rect.adjusted(-3, -3, 3, 3)

    def _update_input_region(self) -> None:
        # XShape работает в физических пикселях, а Qt рисует в логических: при масштабе
        # GNOME 125 % (Xft.dpi 120) без пересчёта зона кликов уезжала мимо капсулы.
        dpr = self.devicePixelRatioF() or 1.0
        logical = self._interactive_rect()
        rect = QRectF(logical.x() * dpr, logical.y() * dpr, logical.width() * dpr,
                      logical.height() * dpr).toAlignedRect()
        if rect == self._input_rect or not self.isVisible():
            return
        self._input_rect = rect
        try:
            if self._xshape is None:
                from Xlib import display
                dpy = display.Display()
                if not dpy.has_extension("SHAPE"):
                    self._xshape = False
                    return
                self._xshape = dpy
            if self._xshape is False:
                return
            from Xlib import X
            from Xlib.ext import shape
            win = self._xshape.create_resource_object("window", int(self.winId()))
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0,
                                 [(rect.x(), rect.y(), rect.width(), rect.height())])
            self._xshape.flush()
        except Exception:  # noqa: BLE001
            log.debug("XShape недоступен", exc_info=True)
            self._xshape = False

    # ------------------------------------------------------------------ рисование
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setOpacity(max(0.0, min(1.0, self.appear)))
        glass = self.settings.get("bubble.style", "glass") == "glass"
        rect = self.pill_rect()
        self._draw_pill(p, rect, glass)
        if self.orb > 0.01:
            self._draw_orb(p, rect, glass)
        if self.wave > 0.01 or self.proc > 0.01:
            self._draw_wave(p, rect)
        if self.stop > 0.01:
            self._draw_stop(p, rect)
        if self.state == "loading" or (self.model_loading and self.state == "idle"):
            self._draw_loading(p, rect)
        if self.msg > 0.01 and self.message:
            self._draw_message(p, rect)
        if self.settings.get("bubble.demo_mode", False) and self.wave > 0.3:
            self._draw_demo_label(p, rect)
        top = rect.top() if not self._anchor_top() else rect.bottom()
        top = self._draw_live_text(p, rect, top, glass)
        self._draw_chip(p, rect, top, glass)
        if self.hover > 0.01 and self.state == "idle":
            self._draw_hint(p, rect, glass)
        p.end()

    def _shadow(self, p: QPainter, rect: QRectF, radius: float, strength: float) -> None:
        p.setPen(Qt.NoPen)
        for i in range(7, 0, -1):
            grow = i * 1.6
            alpha = strength * (1 - i / 8) ** 2 * 0.22
            p.setBrush(QColor(0, 0, 0, int(255 * alpha)))
            r = rect.adjusted(-grow, -grow + 2.5, grow, grow + 2.5)
            p.drawRoundedRect(r, radius + grow, radius + grow)

    def _glass_fill(self, p: QPainter, rect: QRectF, radius: float, glass: bool, alpha: float = 1.0,
                    tint: QColor | None = None) -> None:
        self._shadow(p, rect, radius, alpha * (1.0 if rect.height() > 16 else 0.6))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        if glass:
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor(58, 66, 84, int(200 * alpha)))
            grad.setColorAt(0.55, QColor(28, 32, 43, int(212 * alpha)))
            grad.setColorAt(1.0, QColor(17, 20, 28, int(226 * alpha)))
        else:
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, QColor(16, 16, 18, int(240 * alpha)))
            grad.setColorAt(1.0, QColor(6, 6, 8, int(245 * alpha)))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        if tint is not None:
            p.setBrush(tint)
            p.drawPath(path)
        # Блик сверху и тонкая рамка — «стекло».
        edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top_a = 0.34 if glass else 0.14
        edge.setColorAt(0.0, QColor(255, 255, 255, int(255 * top_a * alpha)))
        edge.setColorAt(0.5, QColor(255, 255, 255, int(255 * 0.07 * alpha)))
        edge.setColorAt(1.0, QColor(255, 255, 255, int(255 * (0.12 if glass else 0.06) * alpha)))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        if glass and rect.height() > 16:
            sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.center().y()))
            sheen.setColorAt(0.0, QColor(255, 255, 255, int(28 * alpha)))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(sheen))
            inner = QPainterPath()
            inner.addRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -rect.height() / 2), radius - 1.5, radius - 1.5)
            p.drawPath(inner)

    def _draw_pill(self, p: QPainter, rect: QRectF, glass: bool) -> None:
        radius = rect.height() / 2
        alpha = 0.78 + 0.22 * min(1.0, (rect.height() - 8) / 20 + self.hover)
        tint = None
        if self.success > 0.02:
            tint = rgba(self.settings.get("bubble.accent", "#3A8DFF"), 0.35 * self.success)
        self._glass_fill(p, rect, radius, glass, alpha, tint)
        if self.state == "idle" and self.orb < 0.5:
            # Три точки внутри капсулы при наведении.
            if self.hover > 0.05:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, int(200 * self.hover)))
                for i in (-1, 0, 1):
                    p.drawEllipse(QPointF(rect.center().x() + i * 8, rect.center().y()), 1.6, 1.6)

    def _orb_center(self, rect: QRectF) -> QPointF:
        return QPointF(rect.left() + 8 + 11, rect.center().y())

    def _draw_orb(self, p: QPainter, rect: QRectF, glass: bool) -> None:
        a = self.orb
        accent = QColor(self.settings.get("bubble.accent", "#3A8DFF"))
        c = self._orb_center(rect)
        speaking = self.level if self.state in ("listening", "hands_free") else 0.0
        breathe = 0.04 * math.sin(self.pulse * 2.4) if self.state == "processing" else 0.0
        r = 10.5 * (0.82 + 0.18 * a) * (1.0 + 0.17 * speaking + breathe)
        p.save()
        p.setOpacity(p.opacity() * a)
        # Свечение
        glow = QRadialGradient(c, r * 2.4)
        glow_a = 0.20 + 0.55 * speaking + (0.18 if self.state == "processing" else 0.0)
        glow.setColorAt(0.0, rgba(accent.name(), glow_a))
        glow.setColorAt(0.45, rgba(accent.name(), glow_a * 0.35))
        glow.setColorAt(1.0, rgba(accent.name(), 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, r * 2.4, r * 2.4)
        if not glass:
            # Классический вид: белая точка-микрофон.
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(c, r * 0.55, r * 0.55)
            p.restore()
            return
        # Тело шара
        body = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.45), r * 1.45)
        body.setColorAt(0.0, QColor("#F2FBFF"))
        body.setColorAt(0.28, QColor("#9FE0FF"))
        body.setColorAt(0.62, accent)
        body.setColorAt(1.0, QColor("#1238B8"))
        p.setBrush(QBrush(body))
        p.drawEllipse(c, r, r)
        # «Жидкость» внутри: вращающиеся пятна.
        clip = QPainterPath()
        clip.addEllipse(c, r, r)
        p.setClipPath(clip)
        for i, (col, alpha, scale) in enumerate(((QColor("#6CF2FF"), 0.55, 0.62),
                                                 (QColor("#3D5BFF"), 0.45, 0.70),
                                                 (QColor("#FFFFFF"), 0.30, 0.38))):
            ang = self.phase * (1.0 + 0.35 * i) + i * 2.1
            ox = math.cos(ang) * r * 0.42
            oy = math.sin(ang * 1.3) * r * 0.36
            blob = QRadialGradient(QPointF(c.x() + ox, c.y() + oy), r * scale)
            blob.setColorAt(0.0, rgba(col.name(), alpha))
            blob.setColorAt(1.0, rgba(col.name(), 0.0))
            p.setBrush(QBrush(blob))
            p.drawEllipse(QPointF(c.x() + ox, c.y() + oy), r * scale, r * scale)
        if self.state in ("processing", "loading"):
            cone = QConicalGradient(c, -math.degrees(self.phase * 2.2))
            cone.setColorAt(0.0, QColor(255, 255, 255, 120))
            cone.setColorAt(0.25, QColor(255, 255, 255, 0))
            cone.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(cone))
            p.drawEllipse(c, r, r)
        p.setClipping(False)
        # Блик и ободок
        p.setBrush(QColor(255, 255, 255, 170))
        p.drawEllipse(QPointF(c.x() - r * 0.38, c.y() - r * 0.48), r * 0.30, r * 0.18)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 70), 0.8))
        p.drawEllipse(c, r, r)
        p.restore()

    def _wave_area(self, rect: QRectF) -> QRectF:
        left = rect.left() + 38
        right = rect.right() - 14 - (30 * self.stop)
        if self.settings.get("bubble.demo_mode", False):
            right -= self._text_width("Диктовка с Aqua Linux", self.font_small) + 14
        return QRectF(left, rect.top() + 7, max(10.0, right - left), rect.height() - 14)

    def _draw_wave(self, p: QPainter, rect: QRectF) -> None:
        area = self._wave_area(rect)
        n_visible = max(4, int(area.width() // 6.2))
        step = area.width() / n_visible
        bars = list(self.bars)[-(n_visible + 1):]
        while len(bars) < n_visible + 1:
            bars.insert(0, 0.0)
        cy = area.center().y()
        max_h = area.height()
        p.save()
        p.setPen(Qt.NoPen)
        wave_a = max(self.wave, self.proc)
        for i in range(n_visible + 1):
            x = area.left() + (i - self.bar_shift) * step + step / 2
            if x < area.left() - 1 or x > area.right() + 1:
                continue
            value = bars[i]
            # Обработка: столбики → точки с бегущим бликом.
            shimmer = 0.5 + 0.5 * math.sin(self.phase * 3.0 - i * 0.55)
            proc_h = 3.2 + 3.0 * shimmer ** 3
            live_h = 3.2 + (max_h - 3.2) * min(1.0, value ** 0.75 * 1.15)
            h = live_h * (1 - self.proc) + proc_h * self.proc
            edge_fade = min(1.0, (x - area.left()) / (step * 3.0), (area.right() - x) / (step * 1.2))
            edge_fade = max(0.0, edge_fade)
            alpha = (0.55 + 0.45 * min(1.0, value * 1.6)) * (1 - self.proc) + (0.35 + 0.6 * shimmer) * self.proc
            p.setBrush(QColor(255, 255, 255, int(255 * alpha * edge_fade * wave_a)))
            w = 3.0
            p.drawRoundedRect(QRectF(x - w / 2, cy - h / 2, w, h), w / 2, w / 2)
        p.restore()

    def stop_button_rect(self) -> QRectF:
        rect = self.pill_rect()
        d = 24
        return QRectF(rect.right() - 7 - d, rect.center().y() - d / 2, d, d)

    def _draw_stop(self, p: QPainter, rect: QRectF) -> None:
        r = self.stop_button_rect()
        c = r.center()
        a = self.stop
        p.save()
        p.setOpacity(p.opacity() * a)
        pulse = 0.5 + 0.5 * math.sin(self.pulse * 4.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 69, 58, int(70 * pulse)))
        p.drawEllipse(c, r.width() / 2 + 2.5 + pulse * 1.5, r.height() / 2 + 2.5 + pulse * 1.5)
        grad = QRadialGradient(QPointF(c.x() - 3, c.y() - 4), r.width() * 0.75)
        grad.setColorAt(0.0, QColor("#FF7A70"))
        grad.setColorAt(1.0, QColor("#E5261C"))
        p.setBrush(QBrush(grad))
        p.drawEllipse(c, r.width() / 2 * (0.7 + 0.3 * a), r.height() / 2 * (0.7 + 0.3 * a))
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawRoundedRect(QRectF(c.x() - 3.6, c.y() - 3.6, 7.2, 7.2), 1.6, 1.6)
        p.restore()

    def _draw_loading(self, p: QPainter, rect: QRectF) -> None:
        if self.state == "loading":
            p.setPen(QColor(255, 255, 255, 225))
            p.setFont(self.font_small)
            text_rect = QRectF(rect.left() + 38, rect.top(), rect.width() - 48, rect.height())
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, "Загружаю модель…")
            return
        # Бегущий блик по маленькой капсуле, пока модель грузится.
        x = rect.left() + (self.pulse * 0.9 % 1.0) * rect.width()
        grad = QLinearGradient(QPointF(x - 14, 0), QPointF(x + 14, 0))
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, QColor(140, 210, 255, 170))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        p.save()
        p.setClipPath(path)
        p.fillRect(rect, QBrush(grad))
        p.restore()

    def _draw_message(self, p: QPainter, rect: QRectF) -> None:
        p.save()
        p.setOpacity(p.opacity() * self.msg)
        color = {"error": QColor("#FF5A4F"), "warn": QColor("#FFB340")}.get(
            self.message_kind, QColor(self.settings.get("bubble.accent", "#3A8DFF")))
        c = QPointF(rect.left() + 20, rect.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 4.5, 4.5)
        p.setPen(QColor(255, 255, 255, 235))
        p.setFont(self.font_small)
        p.drawText(QRectF(rect.left() + 34, rect.top(), rect.width() - 44, rect.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self.message)
        p.restore()

    def _draw_demo_label(self, p: QPainter, rect: QRectF) -> None:
        p.save()
        p.setOpacity(p.opacity() * self.wave)
        p.setPen(QColor(255, 255, 255, 220))
        p.setFont(self.font_small)
        width = self._text_width("Диктовка с Aqua Linux", self.font_small)
        right = rect.right() - 14 - 30 * self.stop
        p.drawText(QRectF(right - width, rect.top(), width, rect.height()),
                   Qt.AlignVCenter | Qt.AlignRight, "Диктовка с Aqua Linux")
        p.restore()

    def _wrap_tail(self, text: str, width: float, max_lines: int = 2) -> list[str]:
        fm = QFontMetricsF(self.font_text)
        words = text.replace("\n", " ⏎ ").split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if fm.horizontalAdvance(candidate) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            lines[0] = "…" + lines[0]
        return lines

    def _draw_live_text(self, p: QPainter, rect: QRectF, edge: float, glass: bool) -> float:
        if self.text_alpha < 0.01 or not self.live_text:
            return edge
        max_w = WIN_W - 2 * EDGE - 24
        lines = self._wrap_tail(self.live_text, max_w - 28)
        fm = QFontMetricsF(self.font_text)
        width = min(max_w, max(fm.horizontalAdvance(line) for line in lines) + 28)
        height = len(lines) * 20 + 16
        self.text_w.target, self.text_h.target = width, height
        if self.text_w.value < 1:
            self.text_w.snap(width)
            self.text_h.snap(height)
        self.text_w.step(0.007)
        self.text_h.step(0.007)
        w, h = self.text_w.value, self.text_h.value
        a = self.text_alpha
        slide = (1 - a) * 6
        if self._anchor_top():
            panel = QRectF(WIN_W / 2 - w / 2, edge + 8 + slide, w, h)
            new_edge = panel.bottom()
        else:
            panel = QRectF(WIN_W / 2 - w / 2, edge - 8 - h + slide, w, h)
            new_edge = panel.top()
        p.save()
        p.setOpacity(p.opacity() * a)
        self._glass_fill(p, panel, 14, glass)
        p.setFont(self.font_text)
        p.setPen(QColor(255, 255, 255, 240))
        y = panel.top() + 8
        for line in lines:
            p.drawText(QRectF(panel.left() + 14, y, panel.width() - 28, 20), Qt.AlignVCenter | Qt.AlignLeft, line)
            y += 20
        p.restore()
        return new_edge

    def _draw_chip(self, p: QPainter, rect: QRectF, edge: float, glass: bool) -> None:
        if self.chip_alpha < 0.01 or not self.chip_text:
            return
        text = f"✎  {self.chip_text}"
        width = self._text_width(text, self.font_small) + 26
        h = 26
        a = self.chip_alpha
        slide = (1 - a) * 6
        if self._anchor_top():
            chip = QRectF(WIN_W / 2 - width / 2, edge + 8 + slide, width, h)
        else:
            chip = QRectF(WIN_W / 2 - width / 2, edge - 8 - h + slide, width, h)
        p.save()
        p.setOpacity(p.opacity() * a)
        accent = rgba(self.settings.get("bubble.accent", "#3A8DFF"), 0.22)
        self._glass_fill(p, chip, h / 2, glass, 1.0, accent)
        p.setPen(QColor(255, 255, 255, 235))
        p.setFont(self.font_small)
        p.drawText(chip, Qt.AlignCenter, text)
        p.restore()

    def _draw_hint(self, p: QPainter, rect: QRectF, glass: bool) -> None:
        text = self.hint if not self.model_loading else "Загружаю модель распознавания…"
        width = self._text_width(text, self.font_small) + 24
        h = 26
        a = self.hover
        if self._anchor_top():
            box = QRectF(WIN_W / 2 - width / 2, rect.bottom() + 10 + (1 - a) * 5, width, h)
        else:
            box = QRectF(WIN_W / 2 - width / 2, rect.top() - 10 - h + (1 - a) * 5, width, h)
        p.save()
        p.setOpacity(p.opacity() * a)
        self._glass_fill(p, box, h / 2, glass)
        p.setPen(QColor(255, 255, 255, 230))
        p.setFont(self.font_small)
        p.drawText(box, Qt.AlignCenter, text)
        p.restore()

    # ---------------------------------------------------------------- мышь
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.apply_targets()
        self._kick()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.apply_targets()
        self._kick()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_origin = (int(self.settings.get("bubble.offset_x", 0)),
                                 int(self.settings.get("bubble.offset_y", 0)))
            self._dragging = False

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() > 5:
            self._dragging = True
        if self._dragging:
            ox, oy = self._drag_origin
            self.settings.set("bubble.offset_x", ox + delta.x(), save=False)
            self.settings.set("bubble.offset_y", oy + delta.y(), save=False)
            self.reposition()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._press_pos is None:
            return
        was_drag = self._dragging
        self._press_pos = None
        self._dragging = False
        if was_drag:
            self.settings.save()
            self.moved.emit(int(self.settings.get("bubble.offset_x", 0)), int(self.settings.get("bubble.offset_y", 0)))
            return
        pos = event.position()
        if self.state == "hands_free" and self.stop_button_rect().adjusted(-4, -4, 4, 4).contains(pos):
            self.stop_clicked.emit()
        elif self.state in ("idle", "processing"):
            self.clicked.emit()     # новая длинная запись (можно, пока предыдущая ещё обрабатывается)
        elif self.state in ("listening", "hands_free"):
            self.stop_clicked.emit()
        elif self.state == "message":
            self._msg_until = 0

    def reset_position(self) -> None:
        self.settings.set("bubble.offset_x", 0, save=False)
        self.settings.set("bubble.offset_y", 0)
        self.reposition()
