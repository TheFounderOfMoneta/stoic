"""Значок в верхней панели: открыть ленту, собрать сейчас, выйти. Точка — есть непрочитанное главное."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import APP_NAME
from .theme import ACCENT


def tray_pixmap(unread: bool = False, size: int = 44) -> QPixmap:
    """Монохромные три строки ленты (как значки GNOME) и синяя точка, если есть новое."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    p.setPen(QPen(QColor("#FFFFFF"), s * 0.1, Qt.SolidLine, Qt.RoundCap))
    for y, x2 in ((0.28, 0.8), (0.5, 0.8), (0.72, 0.55)):
        p.drawLine(QPointF(s * 0.2, s * y), QPointF(s * x2, s * y))
    if unread:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QRectF(s * 0.64, s * 0.6, s * 0.26, s * 0.26))
    p.end()
    return pm


class Tray:
    def __init__(self, controller):
        self.c = controller
        self.icon = QSystemTrayIcon(QIcon(tray_pixmap(False)))
        self.icon.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Открыть ленту", self.show_window)
        menu.addAction("Собрать сейчас", lambda: self.c.collect_now())
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

    def set_unread(self, unread: bool) -> None:
        self.icon.setIcon(QIcon(tray_pixmap(unread)))

    def message(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, QSystemTrayIcon.Information, 8000)
