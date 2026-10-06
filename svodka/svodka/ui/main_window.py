"""Главное окно: Лента · Поиск · Сохранённое · Настройки, и Читалка во всё окно.

Принцип Aqua: на виду — только нужное. Проблемы показываются баннером на Ленте с кнопкой,
которая их исправляет. При первом заходе на страницу — подсказки; «?» — повторить.
"""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..config import APP_NAME, APP_VERSION
from . import widgets as W
from .feed_view import FeedList, feed_rows
from .look import SIDEBAR_W, app_icon, app_icon_pixmap, nav_icon, stylesheet
from .pages import Page, SavedPage, SearchPage, SettingsPage
from .reader import ReaderView
from .theme import palette

PAGES = [("feed", "Лента"), ("search", "Поиск"), ("saved", "Сохранённое"), ("settings", "Настройки")]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


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
        p.fillRect(side + (1 if side else 0), 0, self.width() - side, self.height(), W.pc("window_solid"))
        p.end()


class FeedPage(Page):
    def __init__(self, controller):
        super().__init__("Лента", "", scroll=False)
        self.c = controller
        refresh = QPushButton("Собрать сейчас")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(lambda: self.c.collect_now())
        self.refresh_btn = refresh
        self.head_right.addWidget(refresh, 0, Qt.AlignTop)
        self.notices = QVBoxLayout()
        self.notices.setSpacing(8)
        self.lay.addLayout(self.notices)
        self.progress = QLabel("")
        self.progress.setObjectName("Hint")
        self.progress.hide()
        self.lay.addWidget(self.progress)
        self.empty = QLabel("")
        self.empty.setObjectName("Hint")
        self.empty.setWordWrap(True)
        self.empty.hide()
        self.lay.addWidget(self.empty)
        self.list = FeedList()
        self.list.open_article.connect(self.c.open_article)
        self.list.action.connect(self.c.feed_action)
        self.list.expanded.connect(lambda aid: self.c.storage.log_event(aid, "expand"))
        self.list.on_impression = self.c.log_impression
        self.lay.addWidget(self.list, 1)

    def set_feed(self, feed) -> None:
        today = dt.date.today()
        new = len([it for it in feed.main + feed.more if it.article.get("status") == "new"])
        self.subtitle.setText(f"{WEEKDAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}"
                              + (f" · новых: {new}" if new else ""))
        rows = feed_rows(feed, more_title="ЕЩЁ СВЕЖЕЕ")
        self.list.set_rows(rows)
        if not rows:
            self.empty.setText("Лента пока пустая. Первый сбор обычно занимает 3–6 минут — Claude ищет, читает и "
                               "отбирает статьи по вашим темам. Можно нажать «Собрать сейчас».")
        self.empty.setVisible(not rows)

    def render_notices(self, notices: dict) -> None:
        while self.notices.count():
            item = self.notices.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        for key, (kind, text, label, fn) in notices.items():
            row = QHBoxLayout()
            banner = QLabel(text)
            banner.setObjectName("BannerError" if kind == "error" else "Banner")
            banner.setWordWrap(True)
            row.addWidget(banner, 1)
            if label and fn:
                b = QPushButton(label)
                b.setObjectName("Primary")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(fn)
                row.addWidget(b, 0, Qt.AlignVCenter)
            self.notices.addLayout(row)


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
        self.label.setMaximumWidth(460)
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
        self.colors = palette(controller.settings.get("ui.theme", "auto"))
        W.PALETTE.clear()
        W.PALETTE.update(self.colors)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1180, 800)
        self.setMinimumSize(860, 580)
        self.setStyleSheet(stylesheet(self.colors, controller.settings.get("ui.text_size", "m")))
        self._coach = None
        self._tips_key = ""
        self._tips_timer = QTimer(self)          # таймер принадлежит окну — не сработает после его закрытия
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
        self.feed_page = FeedPage(controller)
        self.search_page = SearchPage(controller)
        self.saved_page = SavedPage(controller)
        self.settings_page = SettingsPage(controller)
        self.reader = ReaderView(controller)
        self.reader.back.connect(self.close_reader)
        self.reader.open_other.connect(self.open_reader)
        self.pages = {"feed": self.feed_page, "search": self.search_page, "saved": self.saved_page,
                      "settings": self.settings_page}
        for key, page in self.pages.items():
            self.stack.addWidget(page.area)
            page.help.clicked.connect(lambda _=False, k=key: self.show_tips(k))
        self.stack.addWidget(self.reader)
        self.current = "feed"
        self._tray_hint_shown = False
        self.toast_box = Toast(self.root)
        self.open_page("feed")

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
        for key, label in PAGES:
            b = QPushButton(f" {label}")
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
    def open_page(self, key: str) -> None:
        if self.stack.currentWidget() is self.reader:
            self.reader.close_session()
        self.sidebar.show()
        self.current = key
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key].area)
        if key == "saved":
            self.saved_page.refresh()
        elif key == "settings":
            self.settings_page.rebuild()
        elif key == "search":
            self.search_page.box.setFocus()
        self.root.update()
        if self.c.settings.get("ui.welcome_done") and key not in (self.c.settings.get("ui.tips_seen") or []):
            self._tips_key = key
            self._tips_timer.start(400)

    def open_reader(self, article_id: int) -> None:
        self.sidebar.hide()
        self.stack.setCurrentWidget(self.reader)
        self.reader.open(article_id)
        self.reader.setFocus()
        self.root.update()

    def close_reader(self) -> None:
        self.reader.close_session()
        self.open_page(self.current if self.current in self.pages else "feed")
        self.c.after_reading()

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
        """Закрытие окна: если есть значок в панели — «Сводка» остаётся там (уведомления о свежем)."""
        if self.stack.currentWidget() is self.reader:
            self.close_reader()
        if self.c.tray is not None:
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.c.tray.message("Сводка работает в фоне",
                                    "Значок в верхней панели — открыть ленту или выйти.")
            return
        event.accept()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def apply_theme(self) -> None:
        """Сменить тему/размер текста на лету, без перезапуска."""
        self.colors = palette(self.c.settings.get("ui.theme", "auto"))
        W.PALETTE.clear()
        W.PALETTE.update(self.colors)
        self.setStyleSheet(stylesheet(self.colors, self.c.settings.get("ui.text_size", "m")))
        for key, b in self.nav_buttons.items():
            b.setIcon(nav_icon(key, self.colors["accent"] if b.isChecked() else self.colors["muted"]))
        for page in (self.feed_page, self.search_page, self.saved_page):
            page.list._relayout()
        self.root.update()

    # ------------------------------------------------------------- подсказки
    def show_tips(self, key: str) -> None:
        if self._coach is not None:
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
        if key == "feed":
            return [
                (self.feed_page.list, "Ваша лента",
                 "Claude собирает статьи утром и вечером. Порядок подстраивается под вас: что вы открываете, "
                 "дочитываете, сохраняете и отвергаете. «Главное» — лучшее на сейчас."),
                (self.feed_page.list, "Наведите на статью",
                 "Появятся «Интересно», «Не моё», «Сохранить» и строка «Почему здесь». Отметка «Разведка» — "
                 "новое для вас: так лента ищет интересы, о которых вы не говорили."),
                (self.feed_page.refresh_btn, "Сбор вручную",
                 "Обычно не нужен: лента обновляется сама, а если ПК был выключен — сразу после включения."),
            ]
        if key == "search":
            return [(self.search_page.box, "Поиск",
                     "Ищет по вашей библиотеке с учётом окончаний. Если нужно свежее — «Искать в интернете с Claude»: "
                     "найденное переведётся и появится и здесь, и в Ленте.")]
        if key == "settings":
            return [(None, "Настройки",
                     "Здесь темы, профиль и расписание. В «Обучении ленты» видно, насколько лента вас понимает, "
                     "и можно импортировать интересы из YouTube и поиска Google.")]
        return []
