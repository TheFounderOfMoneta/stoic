"""Значок в верхней панели: открыть, начать сессию, выйти."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import APP_NAME


def tray_pixmap(size: int = 44) -> QPixmap:
    """Монохромная раскрытая книга (как значки GNOME)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    p.setPen(QPen(QColor("#FFFFFF"), s * 0.08, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for side in (-1, 1):
        path = QPainterPath()
        path.moveTo(s * 0.5, s * 0.8)
        path.lineTo(s * (0.5 + side * 0.36), s * 0.72)
        path.lineTo(s * (0.5 + side * 0.36), s * 0.22)
        path.lineTo(s * 0.5, s * 0.3)
        p.drawPath(path)
    p.drawLine(QPointF(s * 0.5, s * 0.3), QPointF(s * 0.5, s * 0.8))
    p.end()
    return pm


class Tray:
    def __init__(self, controller):
        self.c = controller
        self.icon = QSystemTrayIcon(QIcon(tray_pixmap()))
        self.icon.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Открыть", self.show_window)
        menu.addAction("Начать сессию", self._start)
        menu.addSeparator()
        menu.addAction("Выйти", QApplication.quit)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        self.c.window.bring_to_front()

    def _start(self) -> None:
        self.show_window()
        topic = self.c.current_topic()
        if topic:
            self.c.start_learning(topic["id"])

    def message(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, QSystemTrayIcon.Information, 8000)
