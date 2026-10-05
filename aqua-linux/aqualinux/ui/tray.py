"""Значок в трее (AppIndicator в Ubuntu) и общее меню."""
from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..config import APP_NAME
from .theme import palette, stylesheet, tray_pixmap


def build_menu(app, parent=None) -> QMenu:
    """Короткое меню: только то, что нужно каждый день."""
    menu = QMenu(parent)
    menu.setStyleSheet(stylesheet(palette(app.settings.get("ui.theme", "auto"))))
    if app.rec is not None:
        menu.addAction("Закончить и вставить", app.finish)
        menu.addAction("Отменить запись", app.cancel)
    else:
        menu.addAction("Начать диктовку", lambda: app.start("hands_free"))
    menu.addAction("Вставить последнее ещё раз", app.paste_last)
    menu.addSeparator()

    # Микрофон — выбор в одно касание, с понятными названиями из PipeWire.
    from ..audio import list_input_devices
    current = app.settings.get("audio.input_device")
    devices = list_input_devices()
    mic_label = next((label for value, label in devices if value == current), "Как в системе")
    mics = menu.addMenu(f"Микрофон: {mic_label}" if len(mic_label) <= 34 else "Микрофон")
    group = QActionGroup(mics)
    group.setExclusive(True)
    for value, label in devices:
        act = QAction(label, mics)
        act.setCheckable(True)
        act.setChecked(value == current)
        act.triggered.connect(lambda _=False, v=value: app.set_microphone(v))
        group.addAction(act)
        mics.addAction(act)

    ai = QAction("Улучшать текст с помощью ИИ", menu)
    ai.setCheckable(True)
    ai.setChecked(bool(app.settings.get("llm.correct", False)))
    ai.toggled.connect(app.set_ai)
    menu.addAction(ai)
    menu.addSeparator()

    menu.addAction("Открыть Aqua", lambda: app.show_window("home"))
    menu.addAction("Настройки", lambda: app.show_window("settings"))
    pause = QAction("Приостановить", menu)
    pause.setCheckable(True)
    pause.setChecked(not app.settings.get("hotkeys.enabled", True))
    pause.toggled.connect(lambda v: app.settings.set("hotkeys.enabled", not v))
    menu.addAction(pause)
    if app.bubble.isVisible() and (app.settings.get("bubble.offset_x") or app.settings.get("bubble.offset_y")):
        menu.addAction("Вернуть капсулу на место", app.bubble.reset_position)
    menu.addSeparator()
    menu.addAction("Выйти", app.quit_app)
    return menu


class Tray(QSystemTrayIcon):
    def __init__(self, app):
        # Монохромный значок — как остальные символьные значки в верхней панели GNOME.
        icons = {state: QIcon(tray_pixmap(state)) for state in
                 ("ready", "loading", "downloading", "error", "unloaded", "recording")}
        super().__init__(icons["loading"])
        self._icons = icons
        self.app = app
        self._state = "loading"
        self.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._rebuild)
        self.setContextMenu(self.menu)
        self.activated.connect(self._activated)
        self._rebuild()

    def _rebuild(self) -> None:
        self.menu.clear()
        fresh = build_menu(self.app)
        for action in fresh.actions():
            action.setParent(self.menu)
            self.menu.addAction(action)
        self.menu.setStyleSheet(fresh.styleSheet())
        self._keep = fresh

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.app.show_window("home")

    def set_status(self, state: str, message: str) -> None:
        self._state = state
        self.setIcon(self._icons.get(state, self._icons["ready"]))
        self.setToolTip(f"{APP_NAME}\n{message}")

    def set_recording(self, recording: bool) -> None:
        self.setIcon(self._icons["recording"] if recording else self._icons.get(self._state, self._icons["ready"]))
