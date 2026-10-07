"""Главное окно: Главная · Учёба · Повторение · Прогресс · Разговор · Настройки.

Сессия учёбы и разговор открываются во всё окно (сайдбар скрыт) — ничего не отвлекает.
Принцип Aqua: на виду только нужное; при первом заходе на страницу — подсказки, «?» — повторить.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION
from . import widgets as W
from .learn_view import LearnPage, LearnSessionView
from .look import SIDEBAR_W, app_icon, app_icon_pixmap, load_fonts, nav_icon, stylesheet
from .pages import HomePage, ProgressPage, SettingsPage
from .review_view import ReviewPage
from .talk_view import TalkPage, TalkSessionView
from .theme import palette

PAGES = [("home", "Главная"), ("learn", "Учёба"), ("review", "Повторение"), ("progress", "Прогресс"),
         ("talk", "Разговор"), ("settings", "Настройки")]


class Backdrop(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.win = window

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        side = SIDEBAR_W if self.win.sidebar.isVisible() else 0
        p.fillRect(self.rect(), W.pc("window_solid"))
        if side:
            p.fillRect(0, 0, side, self.height(), W.pc("sidebar_solid"))
            p.fillRect(side, 0, 1, self.height(), W.pc("hairline"))
        p.end()


class Toast(QFrame):
    """Короткое сообщение внизу окна, иногда с кнопкой («Отменить»)."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("Toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 10, 10)
        lay.setSpacing(12)
        self.label = QLabel("")
        self.label.setObjectName("ToastText")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(480)
        lay.addWidget(self.label)
        self.action = QPushButton("")
        self.action.setObjectName("Link")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.clicked.connect(self._act)
        lay.addWidget(self.action)
        self._fn = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, text: str, action: str | None = None, fn=None, ms: int = 5000) -> None:
        self.label.setText(text)
        self._fn = fn
        self.action.setText(action or "")
        self.action.setVisible(bool(action and fn))
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self.timer.start(max(1500, ms))

    def place(self) -> None:
        host = self.parentWidget()
        self.adjustSize()
        self.move(max(8, (host.width() - self.width()) // 2), host.height() - self.height() - 22)

    def _act(self) -> None:
        fn, self._fn = self._fn, None
        self.hide()
        if fn:
            fn()


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        load_fonts()
        self.colors = palette(controller.settings.get("ui.theme", "auto"))
        W.PALETTE.clear()
        W.PALETTE.update(self.colors)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1180, 800)
        self.setMinimumSize(880, 600)
        self.setStyleSheet(stylesheet(self.colors, controller.settings.get("ui.text_size", "m")))
        self._coach = None
        self._tips_key = ""
        self._tips_timer = QTimer(self)
        self._tips_timer.setSingleShot(True)
        self._tips_timer.timeout.connect(lambda: self.current == self._tips_key and self.show_tips(self._tips_key))
        self.root = Backdrop(self)
        lay = QHBoxLayout(self.root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sidebar = self._sidebar()
        lay.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(self.root)
        self.home_page = HomePage(controller)
        self.learn_page = LearnPage(controller)
        self.review_page = ReviewPage(controller)
        self.progress_page = ProgressPage(controller)
        self.talk_page = TalkPage(controller)
        self.settings_page = SettingsPage(controller)
        self.pages = {"home": self.home_page, "learn": self.learn_page, "review": self.review_page,
                      "progress": self.progress_page, "talk": self.talk_page, "settings": self.settings_page}
        for key, page in self.pages.items():
            self.stack.addWidget(page.area)
            page.help.clicked.connect(lambda _=False, k=key: self.show_tips(k))
        self.session_view = LearnSessionView(controller)
        self.talk_view = TalkSessionView(controller)
        self.stack.addWidget(self.session_view)
        self.stack.addWidget(self.talk_view)
        self.current = "home"
        self.toast_box = Toast(self.root)
        self.open_page("home")

    # ------------------------------------------------------------- сайдбар
    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(SIDEBAR_W)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 20, 12, 16)
        lay.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon_pixmap(128).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        brand.addWidget(title, 1)
        lay.addLayout(brand)
        lay.addSpacing(20)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for key, text in PAGES:
            if key == "talk":
                lay.addSpacing(14)                 # «Разговор» отдельно от учёбы — и визуально тоже
            b = QPushButton(f" {text}")
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.open_page(k))
            b.toggled.connect(lambda on, btn=b, k=key: btn.setIcon(
                nav_icon(k, self.colors["accent"] if on else self.colors["muted"])))
            b.setIcon(nav_icon(key, self.colors["muted"]))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.side_status = QLabel("")
        self.side_status.setObjectName("SidebarFooter")
        self.side_status.setWordWrap(True)
        self.side_status.setContentsMargins(8, 0, 4, 6)
        lay.addWidget(self.side_status)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 0, 0)
        howto = QPushButton("Как пользоваться")
        howto.setObjectName("Link")
        howto.setCursor(Qt.PointingHandCursor)
        howto.clicked.connect(lambda: self.c.show_welcome())
        foot.addWidget(howto)
        foot.addStretch(1)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("SidebarFooter")
        foot.addWidget(ver)
        lay.addLayout(foot)
        return side

    # ------------------------------------------------------------- навигация
    def open_page(self, key: str, topic_id: int | None = None, ahead: bool = False) -> None:
        if self.stack.currentWidget() is self.review_page.area and key != "review":
            self.c.end_review_session()
        self.sidebar.show()
        self.current = key
        self.nav_buttons[key].setChecked(True)
        page = self.pages[key]
        if key == "home":
            page.refresh()
        elif key == "learn":
            page.refresh()
        elif key == "review":
            page.start(topic_id, ahead)
        elif key == "progress":
            page.refresh()
        elif key == "talk":
            page.refresh()
        elif key == "settings":
            page.rebuild()
        self.stack.setCurrentWidget(page.area)
        self.root.update()
        if self.c.settings.get("ui.welcome_done") and key not in (self.c.settings.get("ui.tips_seen") or []):
            self._tips_key = key
            self._tips_timer.start(400)

    def open_topic(self, topic_id: int) -> None:
        self.open_page("learn")
        self.learn_page.open_topic(topic_id)

    def open_review(self, topic_id: int | None = None, ahead: bool = False) -> None:
        self.open_page("review", topic_id, ahead)

    def open_session(self) -> None:
        self.sidebar.hide()
        self.stack.setCurrentWidget(self.session_view)
        self.session_view.input.edit.setFocus()
        self.root.update()

    def open_talk_session(self) -> None:
        self.sidebar.hide()
        self.stack.setCurrentWidget(self.talk_view)
        self.talk_view.input.edit.setFocus()
        self.root.update()

    def in_focus_mode(self) -> bool:
        return self.stack.currentWidget() in (self.session_view, self.talk_view)

    def set_status(self, text: str) -> None:
        self.side_status.setText(text)

    def toast(self, text: str, action: str | None = None, fn=None, ms: int = 5000) -> None:
        self.toast_box.show_text(text, action, fn, ms)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.toast_box.isVisible():
            self.toast_box.place()

    def bring_to_front(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Закрытие окна: идущая сессия сохраняется (время и ответы уже в базе)."""
        self.c.on_close()
        if self.c.tray is not None:
            event.ignore()
            self.hide()
            return
        event.accept()
        QApplication.quit()

    def apply_theme(self) -> None:
        """Сменить тему/размер текста на лету, без перезапуска."""
        self.colors = palette(self.c.settings.get("ui.theme", "auto"))
        W.PALETTE.clear()
        W.PALETTE.update(self.colors)
        self.setStyleSheet(stylesheet(self.colors, self.c.settings.get("ui.text_size", "m")))
        for key, b in self.nav_buttons.items():
            b.setIcon(nav_icon(key, self.colors["accent"] if b.isChecked() else self.colors["muted"]))
        self.root.update()

    # ------------------------------------------------------------- подсказки
    def show_tips(self, key: str) -> None:
        if self._coach is not None or self.in_focus_mode():
            return
        steps = self._tips(key)
        if not steps:
            return
        self._coach = W.Coach(self.root, steps)
        self._coach.finished.connect(lambda: self._tips_done(key))

    def _tips_done(self, key: str) -> None:
        self._coach = None
        seen = list(self.c.settings.get("ui.tips_seen") or [])
        if key not in seen:
            seen.append(key)
            self.c.settings.set("ui.tips_seen", seen)

    def _tips(self, key: str) -> list:
        if key == "home":
            return [(None, "Главная — «что сейчас»",
                     "Где вы остановились, план на сегодня и одна кнопка. План сам подстраивается: под ваше время, "
                     "точку усталости, пропуски и тяжёлые дни.")]
        if key == "learn":
            return [(self.learn_page.new_btn, "Темы",
                     "Напишите, что изучить и зачем — Claude составит карту понятий. Каждая сессия идёт по "
                     "шаблону: крючок, ядро, практика, по памяти, подарок и вопрос на следующий раз.")]
        if key == "review":
            return [(None, "Повторение",
                     "Карточки возвращаются, когда вы вот-вот их забудете. Сначала вспомните сами, потом "
                     "«Показать ответ» (Пробел) и честная оценка 1–4.")]
        if key == "progress":
            return [(None, "Прогресс",
                     "Всё, что система о вас узнала: время, память, какие форматы на вас работают и не "
                     "затягивает ли учёба сама по себе.")]
        if key == "talk":
            return [(None, "Разговор",
                     "Место, где можно выговориться. Отдельно от учёбы: учёба видит только «сегодня тяжело», "
                     "без содержания. Переписка по умолчанию удаляется.")]
        return []
