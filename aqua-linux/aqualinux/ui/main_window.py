"""Главное окно: Главная · История · Словарь · Настройки.

Принцип: на виду — только то, что нужно обычному человеку. Всё техническое
(модель, видеокарта, тонкие настройки) — в «Настройки → Дополнительно».
При первом заходе на каждую страницу показываются подсказки; «?» — повторить.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QStackedWidget, QTableWidget,
                               QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import desktop, llm
from ..audio import list_input_devices
from ..config import APP_NAME, DATA_DIR, MODELS_DIR, find_model_dir
from . import widgets as W
from .theme import app_icon, app_icon_pixmap, nav_icon, palette, stylesheet
from .widgets import (Card, Coach, Group, HelpButton, HotkeyEditor, OrbWidget, Section, Segmented, StatTile,
                      bind_combo, bind_line, bind_spin, bind_switch, keycaps_row)

SIDEBAR_W = 216
CONTENT_MAX_W = 760
PAGES = [
    ("home", "Главная", "home"),
    ("history", "История", "history"),
    ("dictionary", "Словарь", "dictionary"),
    ("settings", "Настройки", "settings"),
]


def human_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{int(minutes * 60)} с"
    if minutes < 60:
        return f"{minutes:.0f} мин"
    return f"{minutes / 60:.1f} ч".replace(".", ",")


class Backdrop(QWidget):
    """Фон окна: сайдбар и контент (в режиме «стекло» — полупрозрачные для Blur My Shell)."""

    def __init__(self, glass: bool):
        super().__init__()
        self.glass = glass

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(0, 0, SIDEBAR_W, self.height(), W.pc("sidebar" if self.glass else "sidebar_solid"))
        p.fillRect(SIDEBAR_W, 0, self.width() - SIDEBAR_W, self.height(),
                   W.pc("window" if self.glass else "window_solid"))
        p.fillRect(SIDEBAR_W, 0, 1, self.height(), W.pc("hairline"))
        p.end()


class Page:
    """Прокручиваемая страница с колонкой по центру и заголовком с кнопкой «?»."""

    def __init__(self, title: str = "", subtitle: str = ""):
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        outer = QWidget()
        row = QHBoxLayout(outer)
        row.setContentsMargins(36, 30, 36, 40)
        column = QWidget()
        column.setMaximumWidth(CONTENT_MAX_W)
        self.lay = QVBoxLayout(column)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(22)
        row.addStretch(1)
        row.addWidget(column, 100)
        row.addStretch(1)
        self.help = HelpButton()
        if title:
            head = QHBoxLayout()
            texts = QVBoxLayout()
            texts.setSpacing(4)
            t = QLabel(title)
            t.setObjectName("PageTitle")
            texts.addWidget(t)
            if subtitle:
                s = QLabel(subtitle)
                s.setObjectName("PageSubtitle")
                s.setWordWrap(True)
                texts.addWidget(s)
            head.addLayout(texts, 1)
            head.addWidget(self.help, 0, Qt.AlignTop)
            self.lay.addLayout(head)
        self.area.setWidget(outer)


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def when_text(ts: float) -> str:
    stamp = dt.datetime.fromtimestamp(ts)
    today = dt.date.today()
    if stamp.date() == today:
        return "сегодня, " + stamp.strftime("%H:%M")
    if stamp.date() == today - dt.timedelta(days=1):
        return "вчера, " + stamp.strftime("%H:%M")
    return stamp.strftime("%d.%m, %H:%M")


class HistoryItem(QFrame):
    """Запись истории: время и текст. Кнопки появляются при наведении."""

    def __init__(self, window: "MainWindow", row, compact: bool = False):
        super().__init__()
        self.setObjectName("Card")
        self.w = window
        self.row = row
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 12)
        lay.setSpacing(4)
        meta = QHBoxLayout()
        when = QLabel(when_text(row["ts"]))
        when.setObjectName("Muted")
        when.setMinimumHeight(24)
        meta.addWidget(when, 1)
        self.actions = QWidget()
        acts = QHBoxLayout(self.actions)
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(0)
        self._btn(acts, "Копировать", self._copy)
        if not compact:
            if row["audio"] and os.path.exists(row["audio"]):
                self._btn(acts, "▶ Слушать", lambda: self.w.play(row["audio"]))
            self._btn(acts, "Удалить", self._delete).setStyleSheet(f"color: {self.w.colors['danger']};")
        self.actions.setVisible(False)
        meta.addWidget(self.actions)
        lay.addLayout(meta)
        text = row["text"] or "(пусто)"
        if compact and len(text) > 200:
            text = text[:200] + "…"
        self.text = QLabel(text)
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.text)

    def _btn(self, layout, label: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setObjectName("Link")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.row["text"])
        sender = self.sender()
        if sender:
            sender.setText("Скопировано ✓")
            QTimer.singleShot(1200, lambda: sender.setText("Копировать"))

    def _delete(self) -> None:
        self.w.app.history.delete(self.row["id"])
        self.w.refresh_history_views()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.actions.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.actions.setVisible(False)
        super().leaveEvent(e)


class MainWindow(QMainWindow):
    llm_result = Signal(str, bool)
    ds_result = Signal(str, bool, object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = app.settings
        self.colors = palette(self.settings.get("ui.theme", "auto"))
        W.PALETTE.clear()
        W.PALETTE.update(self.colors)
        self.glass = bool(self.settings.get("ui.glass", False))
        if self.glass:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1000, 700)
        self.setMinimumSize(820, 560)
        self.setStyleSheet(stylesheet(self.colors))
        self._coach = None
        self._welcome = None

        self.root = Backdrop(self.glass)
        root_lay = QHBoxLayout(self.root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        self.setCentralWidget(self.root)
        root_lay.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        root_lay.addWidget(self.stack, 1)

        builders = {"home": self._page_home, "history": self._page_history,
                    "dictionary": self._page_dictionary, "settings": self._page_settings}
        self.pages: dict[str, Page] = {}
        for key, _label, _icon in PAGES:
            page = builders[key]()
            page.help.clicked.connect(lambda _=False, k=key: self.show_tips(k))
            self.pages[key] = page
            self.stack.addWidget(page.area)

        app.status_changed.connect(self._on_status)
        app.history_changed.connect(self.refresh_history_views)
        app.mic_level.connect(self._on_mic_level)
        app.llm_state_changed.connect(self._show_ai_state)
        app.notices_changed.connect(self._render_notices)
        self.llm_result.connect(self._on_llm_test)
        self.ds_result.connect(self._on_ds_test)
        self._render_notices()
        self._on_status(*app.engine_state)
        self._show_ai_state(app.local_llm.state, app.local_llm.message, -1)

    def detach(self) -> None:
        """Отключиться от сигналов приложения перед пересозданием окна (смена темы)."""
        for signal, slot in ((self.app.status_changed, self._on_status),
                             (self.app.history_changed, self.refresh_history_views),
                             (self.app.mic_level, self._on_mic_level),
                             (self.app.llm_state_changed, self._show_ai_state),
                             (self.app.notices_changed, self._render_notices)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for editor in getattr(self, "hotkey_editors", []):
            try:
                self.app.hotkey_captured.disconnect(editor._captured)
            except (RuntimeError, TypeError):
                pass

    def current_page(self) -> str:
        current = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p.area is current), "home")

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
        title = QLabel("Aqua")
        title.setObjectName("AppTitle")
        brand.addWidget(title, 1)
        lay.addLayout(brand)
        lay.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for key, label, icon in PAGES:
            btn = QPushButton(f" {label}")
            btn.setObjectName("Nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.open_page(k))
            btn.toggled.connect(lambda on, b=btn, i=icon: b.setIcon(
                nav_icon(i, self.colors["accent"] if on else self.colors["muted"])))
            btn.setIcon(nav_icon(icon, self.colors["muted"]))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            lay.addWidget(btn)
        lay.addStretch(1)
        # Состояние показываем, только если что-то не так (загрузка, ошибка).
        self.side_status = QLabel("")
        self.side_status.setObjectName("SidebarFooter")
        self.side_status.setWordWrap(True)
        self.side_status.setContentsMargins(8, 0, 4, 6)
        lay.addWidget(self.side_status)
        howto = QPushButton("Как пользоваться")
        howto.setObjectName("Link")
        howto.setCursor(Qt.PointingHandCursor)
        howto.clicked.connect(self.show_welcome)
        hl = QHBoxLayout()
        hl.setContentsMargins(4, 0, 0, 0)
        hl.addWidget(howto)
        hl.addStretch(1)
        lay.addLayout(hl)
        return side

    # ------------------------------------------------------------- навигация и обучение
    def open_page(self, key: str) -> None:
        if key not in self.pages:
            key = "home"
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key].area)
        if key in ("home", "history"):
            self.refresh_history_views()
        if key == "settings" and hasattr(self, "mic_combo"):
            self._fill_mics()
        self.show()
        self.raise_()
        self.activateWindow()
        if not self.settings.get("ui.welcome_done", False):
            QTimer.singleShot(150, self.show_welcome)
        elif key not in (self.settings.get("ui.tips_seen") or []):
            QTimer.singleShot(350, lambda: self.show_tips(key))

    def show_welcome(self) -> None:
        if self._welcome is not None:
            return
        if self._coach is not None:
            self._coach.close_coach()
        from .welcome import Welcome
        self._welcome = Welcome(self, self.root)
        self._welcome.finished.connect(self._welcome_done)

    def _welcome_done(self) -> None:
        self._welcome = None
        self.open_page("home")

    def show_tips(self, key: str) -> None:
        if self._coach is not None or self._welcome is not None or self.current_page() != key:
            return
        steps = self._tips(key)
        if not steps:
            return
        self._coach = Coach(self.root, steps)
        self._coach.finished.connect(lambda: self._tips_done(key))

    def _tips_done(self, key: str) -> None:
        self._coach = None
        seen = list(self.settings.get("ui.tips_seen") or [])
        if key not in seen:
            seen.append(key)
            self.settings.set("ui.tips_seen", seen)

    def _tips(self, key: str) -> list:
        act = (self.settings.get("hotkeys.activate") or [["ralt"]])[0]
        from ..hotkeys import pretty_combo
        name = pretty_combo(act)
        if key == "home":
            return [
                (self.hero_hint, "Главное действие",
                 f"Поставьте курсор в любое поле — браузер, Telegram, документ. Удерживайте {name}, говорите, "
                 "отпустите — текст вставится сам."),
                (self.try_card, "Попробуйте прямо здесь",
                 f"Щёлкните в это поле, удерживайте {name} и скажите любую фразу."),
                (self.stats_card, "Ваши успехи", "Сколько слов вы надиктовали и сколько времени сэкономили."),
                (self.nav_buttons["settings"], "Если что-то нужно поменять",
                 "Клавишу, микрофон и остальное можно настроить здесь."),
            ]
        if key == "history":
            return [
                (self.history_search, "Поиск", "Найдите любую прошлую диктовку по словам."),
                (self.history_holder, "Ваши диктовки",
                 "Наведите на запись — появятся кнопки: скопировать, прослушать, удалить."),
            ]
        if key == "dictionary":
            return [
                (self.dict_tabs, "Две вкладки",
                 "«Слова» — научите Aqua писать имена и термины. «Замены» — короткая фраза превращается "
                 "в готовый текст."),
                (self.dict_add_row, "Добавить слово",
                 "Слева — как писать (Kubernetes), справа — как вы его произносите (кубернетис). "
                 "Нажмите «Добавить»."),
                (self.dict_table, "Список", "Дважды щёлкните, чтобы исправить. Крестик — удалить."),
            ]
        if key == "settings":
            return [
                (self.basic_group, "Основное", "Всё самое нужное: клавиша, микрофон, звуки, капсула."),
                (self.advanced_label, "Дополнительно",
                 "Остальное спрятано в блоках ниже — открывайте, только если нужно. "
                 "Там же ИИ-помощник для правки текста голосом."),
            ]
        return []

    def closeEvent(self, event) -> None:  # noqa: N802
        self.app.mic_test(False)
        if self.app.quitting:
            event.accept()
            return
        event.ignore()   # окно прячется, Aqua продолжает работать в фоне
        self.hide()

    # ------------------------------------------------------------- Главная
    def _page_home(self) -> Page:
        pg = Page()
        lay = pg.lay
        hour = dt.datetime.now().hour
        greet = ("Доброе утро" if 5 <= hour < 12 else "Добрый день" if 12 <= hour < 18
                 else "Добрый вечер" if 18 <= hour < 23 else "Доброй ночи")
        hero = QHBoxLayout()
        hero.setSpacing(16)
        self.orb = OrbWidget(76)
        hero.addWidget(self.orb)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addStretch(1)
        h = QLabel(greet)
        h.setObjectName("Hero")
        text.addWidget(h)
        self.hero_hint = QWidget()
        self.hero_hint_lay = QHBoxLayout(self.hero_hint)
        self.hero_hint_lay.setContentsMargins(0, 0, 0, 0)
        text.addWidget(self.hero_hint)
        text.addStretch(1)
        hero.addLayout(text, 1)
        hero.addWidget(pg.help, 0, Qt.AlignTop)
        lay.addLayout(hero)
        self._fill_hero_hint()

        self.banner = QLabel("")
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        lay.addWidget(self.banner)
        # Важные предупреждения с кнопкой «исправить»: видеопамять, перегруз микрофона, DeepSeek.
        self.notice_holder = QWidget()
        self.notice_lay = QVBoxLayout(self.notice_holder)
        self.notice_lay.setContentsMargins(0, 0, 0, 0)
        self.notice_lay.setSpacing(10)
        self.notice_holder.hide()
        lay.addWidget(self.notice_holder)

        self.try_card = Card("Попробуйте здесь", padding=18)
        self.try_box = QPlainTextEdit()
        self.try_box.setPlaceholderText("Щёлкните сюда и продиктуйте что-нибудь…")
        self.try_box.setFixedHeight(84)
        self.try_card.add(self.try_box)
        lay.addWidget(self.try_card)

        self.stats_card = Card(padding=18)
        srow = QHBoxLayout()
        srow.setSpacing(18)
        self.stat_words = StatTile("слов надиктовано")
        self.stat_saved = StatTile("времени сэкономлено")
        self.stat_streak = StatTile("дней подряд")
        for i, tile in enumerate((self.stat_words, self.stat_saved, self.stat_streak)):
            if i:
                line = QFrame()
                line.setObjectName("Divider")
                line.setFixedWidth(1)
                srow.addWidget(line)
            srow.addWidget(tile, 1)
        self.stats_card.add_layout(srow)
        lay.addWidget(self.stats_card)

        head = QHBoxLayout()
        label = QLabel("НЕДАВНЕЕ")
        label.setObjectName("SectionLabel")
        label.setContentsMargins(12, 0, 0, 0)
        head.addWidget(label)
        head.addStretch(1)
        all_link = QPushButton("Вся история →")
        all_link.setObjectName("Link")
        all_link.setCursor(Qt.PointingHandCursor)
        all_link.clicked.connect(lambda: self.open_page("history"))
        head.addWidget(all_link)
        lay.addLayout(head)
        self.recent_body = QVBoxLayout()
        self.recent_body.setSpacing(10)
        lay.addLayout(self.recent_body)
        lay.addStretch(1)
        return pg

    def _fill_hero_hint(self) -> None:
        clear_layout(self.hero_hint_lay)
        combos = self.settings.get("hotkeys.activate") or []
        if combos:
            self.hero_hint_lay.addWidget(keycaps_row(combos[0], "удерживайте и говорите"))
        else:
            label = QLabel("Щёлкните по капсуле над доком и говорите")
            label.setObjectName("Soft")
            self.hero_hint_lay.addWidget(label)

    def _on_mic_level(self, level: float) -> None:
        if self.isVisible():
            self.orb.set_level(level)

    def _refresh_home(self) -> None:
        st = self.app.history.stats(int(self.settings.get("general.typing_wpm_baseline", 40)))
        self.stat_words.set(f"{st['words']:,}".replace(",", " "))
        self.stat_saved.set(human_minutes(st["saved_minutes"]))
        self.stat_streak.set(str(st["streak"]))
        clear_layout(self.recent_body)
        rows = self.app.history.recent(3)
        if not rows:
            empty = QLabel("Здесь появятся ваши последние диктовки.")
            empty.setObjectName("Muted")
            empty.setContentsMargins(12, 0, 0, 0)
            self.recent_body.addWidget(empty)
        for row in rows:
            self.recent_body.addWidget(HistoryItem(self, row, compact=True))

    # ------------------------------------------------------------- История
    def _page_history(self) -> Page:
        pg = Page("История", "Всё, что вы надиктовали. Хранится только на этом компьютере.")
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Поиск")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(lambda: QTimer.singleShot(150, self._refresh_history))
        pg.lay.addWidget(self.history_search)
        self.history_holder = QWidget()
        self.history_list = QVBoxLayout(self.history_holder)
        self.history_list.setContentsMargins(0, 0, 0, 0)
        self.history_list.setSpacing(10)
        pg.lay.addWidget(self.history_holder)
        pg.lay.addStretch(1)
        return pg

    def refresh_history_views(self) -> None:
        if not hasattr(self, "history_list"):
            return
        self._refresh_history()
        self._refresh_home()

    def _refresh_history(self) -> None:
        clear_layout(self.history_list)
        rows = self.app.history.recent(150, self.history_search.text().strip())
        if not rows:
            empty = QLabel("Ничего не найдено." if self.history_search.text() else
                           "Пока пусто — продиктуйте что-нибудь, и запись появится здесь.")
            empty.setObjectName("Muted")
            self.history_list.addWidget(empty)
        for row in rows:
            self.history_list.addWidget(HistoryItem(self, row))

    def play(self, path: str) -> None:
        from ..storage import load_wav
        try:
            import sounddevice as sd
            sd.play(load_wav(path), 16000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Не удалось воспроизвести: {exc}")

    # ------------------------------------------------------------- Словарь и замены
    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers) + 1)
        table.setHorizontalHeaderLabels(headers + [""])
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hh = table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for c in range(len(headers)):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        hh.setSectionResizeMode(len(headers), QHeaderView.Fixed)
        table.setColumnWidth(len(headers), 48)
        table.setMinimumHeight(340)
        return table

    def _remove_cell(self, slot) -> QPushButton:
        rm = QPushButton("✕")
        rm.setObjectName("Icon")
        rm.setToolTip("Удалить")
        rm.setCursor(Qt.PointingHandCursor)
        rm.clicked.connect(slot)
        return rm

    def _add_row(self, left: str, right: str, slot) -> tuple[QWidget, QLineEdit, QLineEdit]:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        a, b = QLineEdit(), QLineEdit()
        a.setPlaceholderText(left)
        b.setPlaceholderText(right)
        add = QPushButton("Добавить")
        add.setObjectName("Primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(slot)
        a.returnPressed.connect(slot)
        b.returnPressed.connect(slot)
        row.addWidget(a, 2)
        row.addWidget(b, 3)
        row.addWidget(add)
        return w, a, b

    def _page_dictionary(self) -> Page:
        pg = Page("Словарь", "Научите Aqua писать имена и термины правильно.")
        self.dict_tabs = Segmented(["Слова", "Замены"])
        pg.lay.addWidget(self.dict_tabs, 0, Qt.AlignLeft)
        self.dict_stack = QStackedWidget()
        self.dict_tabs.changed.connect(self.dict_stack.setCurrentIndex)

        words = QWidget()
        wl = QVBoxLayout(words)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(14)
        self.dict_add_row, self.dict_term, self.dict_sounds = self._add_row(
            "Как писать — Kubernetes", "Как вы произносите — кубернетис", self._dict_add)
        wl.addWidget(self.dict_add_row)
        self.dict_table = self._table(["Как писать", "Как произносите"])
        self.dict_table.itemChanged.connect(self._dict_edited)
        wl.addWidget(self.dict_table)
        self.dict_stack.addWidget(words)

        reps = QWidget()
        rl = QVBoxLayout(reps)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)
        hint = QLabel("Скажите короткую фразу — вставится готовый текст: почта, адрес, ссылка, подпись.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        self.rep_add_row, self.rep_from, self.rep_to = self._add_row(
            "Фраза — мой email", "Текст — name@example.com", self._rep_add)
        rl.addWidget(self.rep_add_row)
        self.rep_table = self._table(["Фраза", "Что вставить"])
        self.rep_table.itemChanged.connect(self._rep_edited)
        rl.addWidget(self.rep_table)
        self.dict_stack.addWidget(reps)

        pg.lay.addWidget(self.dict_stack)
        pg.lay.addStretch(1)
        self._dict_fill()
        self._rep_fill()
        return pg

    def _dict_fill(self) -> None:
        terms = self.app.dictionary.data.setdefault("terms", [])
        self.dict_table.blockSignals(True)
        self.dict_table.setRowCount(len(terms))
        for i, entry in enumerate(terms):
            self.dict_table.setItem(i, 0, QTableWidgetItem(entry.get("term", "")))
            self.dict_table.setItem(i, 1, QTableWidgetItem(", ".join(entry.get("sounds_like", []))))
            self.dict_table.setCellWidget(i, 2, self._remove_cell(lambda _=False, i=i: self._dict_remove(i)))
        self.dict_table.blockSignals(False)

    def _dict_add(self) -> None:
        term = self.dict_term.text().strip()
        if not term:
            return
        sounds = [s.strip() for s in self.dict_sounds.text().split(",") if s.strip()]
        terms = self.app.dictionary.data.setdefault("terms", [])
        if len(terms) >= 800:
            return
        terms.insert(0, {"term": term, "sounds_like": sounds, "fuzzy": True})
        self._dict_save()
        self.dict_term.clear()
        self.dict_sounds.clear()
        self._dict_fill()

    def _dict_edited(self, item: QTableWidgetItem) -> None:
        terms = self.app.dictionary.data["terms"]
        i = item.row()
        if not 0 <= i < len(terms):
            return
        if item.column() == 0:
            terms[i]["term"] = item.text().strip()
        elif item.column() == 1:
            terms[i]["sounds_like"] = [s.strip() for s in item.text().split(",") if s.strip()]
        self._dict_save()

    def _dict_remove(self, i: int) -> None:
        terms = self.app.dictionary.data["terms"]
        if 0 <= i < len(terms):
            terms.pop(i)
            self._dict_save()
            self._dict_fill()

    def _dict_save(self) -> None:
        self.app.dictionary.save()
        self.app.reload_dictionary()

    def _rep_fill(self) -> None:
        reps = self.app.replacements.data.setdefault("replacements", [])
        self.rep_table.blockSignals(True)
        self.rep_table.setRowCount(len(reps))
        for i, rep in enumerate(reps):
            self.rep_table.setItem(i, 0, QTableWidgetItem(rep.get("from", "")))
            self.rep_table.setItem(i, 1, QTableWidgetItem(rep.get("to", "")))
            self.rep_table.setCellWidget(i, 2, self._remove_cell(lambda _=False, i=i: self._rep_remove(i)))
        self.rep_table.blockSignals(False)

    def _rep_add(self) -> None:
        src, dst = self.rep_from.text().strip(), self.rep_to.text()
        if not src:
            return
        self.app.replacements.data.setdefault("replacements", []).insert(
            0, {"from": src, "to": dst, "preserve_case": True, "strip_punct": True})
        self._rep_save()
        self.rep_from.clear()
        self.rep_to.clear()
        self._rep_fill()

    def _rep_edited(self, item: QTableWidgetItem) -> None:
        reps = self.app.replacements.data["replacements"]
        i = item.row()
        if 0 <= i < len(reps):
            reps[i]["from" if item.column() == 0 else "to"] = \
                item.text().strip() if item.column() == 0 else item.text()
            self._rep_save()

    def _rep_remove(self, i: int) -> None:
        reps = self.app.replacements.data["replacements"]
        if 0 <= i < len(reps):
            reps.pop(i)
            self._rep_save()
            self._rep_fill()

    def _rep_save(self) -> None:
        self.app.replacements.save()
        self.app.reload_dictionary()

    # ------------------------------------------------------------- Настройки
    def _page_settings(self) -> Page:
        s = self.settings
        pg = Page("Настройки")
        lay = pg.lay

        basic = Group("Основное")
        self.hotkey_editors = []
        act_editor = HotkeyEditor(self.app, "activate", allow_empty=False, max_bindings=1)
        act_editor.changed.connect(self._fill_hero_hint)
        self.hotkey_editors.append(act_editor)
        basic.add_row("Клавиша для диктовки", "Удерживайте её и говорите.", act_editor)
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(260)
        self._fill_mics()
        self.mic_combo.currentIndexChanged.connect(lambda i: s.set("audio.input_device", self.mic_combo.itemData(i)))
        basic.add_row("Микрофон", "", self.mic_combo)
        self.ai_switch = bind_switch(s, "llm.correct", on_change=self._ai_toggled)
        self.ai_row = basic.add_row("Улучшать текст с помощью ИИ",
                                    "DeepSeek (по ключу) исправляет ошибки распознавания, без интернета — "
                                    "локальная Qwen3.5.", self.ai_switch)
        self.ai_status = QLabel("")
        self.ai_status.setObjectName("Muted")
        self.ai_status.setWordWrap(True)
        self.ai_progress = QProgressBar()
        self.ai_progress.setRange(0, 1000)
        self.ai_progress.setTextVisible(False)
        self.ai_progress.setFixedHeight(4)
        ai_box = QWidget()
        ai_lay = QVBoxLayout(ai_box)
        ai_lay.setContentsMargins(0, 0, 0, 10)
        ai_lay.setSpacing(6)
        ai_lay.addWidget(self.ai_status)
        ai_lay.addWidget(self.ai_progress)
        self.ai_box = ai_box
        basic.rows.addWidget(ai_box)
        basic.add_row("Звуки", "Короткий сигнал в начале и в конце записи.", bind_switch(s, "audio.sounds"))
        basic.add_row("Капсула над доком", "Показывает, что Aqua готова или слушает.", bind_switch(s, "bubble.show"))
        basic.add_row("Запускать вместе с компьютером", "",
                      bind_switch(s, "general.autostart", on_change=lambda v: self._autostart(v)))
        self.basic_group = basic
        lay.addWidget(basic)

        self.advanced_label = QLabel("ДОПОЛНИТЕЛЬНО")
        self.advanced_label.setObjectName("SectionLabel")
        self.advanced_label.setContentsMargins(12, 8, 0, 0)
        lay.addWidget(self.advanced_label)
        adv = QVBoxLayout()
        adv.setSpacing(10)
        lay.addLayout(adv)

        keys = Section("Другие клавиши", "без рук, отмена, вставить снова")
        for key, title, desc in (("hands_free", "Говорить без удержания", "Нажали — говорите, нажали ещё раз — готово."),
                                 ("paste_last", "Вставить последнее ещё раз", ""),
                                 ("cancel", "Отменить запись", "")):
            editor = HotkeyEditor(self.app, key)
            self.hotkey_editors.append(editor)
            keys.add_row(title, desc, editor)
        keys.add_row("Короткое нажатие — длинная запись", "Коротко нажмите клавишу диктовки — можно говорить, "
                                                         "не держа её. Нажмите ещё раз — текст вставится.",
                     bind_switch(s, "hotkeys.double_tap_hands_free"))
        keys.add_row("Не открывать меню по Alt", "Чтобы Alt не открывал меню в Firefox и других программах.",
                     bind_switch(s, "hotkeys.neutralize_modifier"))
        keys.add_row("Короткое нажатие — до", "Дольше — это уже удержание (говорите, пока держите).",
                     bind_spin(s, "hotkeys.tap_threshold_ms", 120, 800, " мс", 20))
        adv.addWidget(keys)

        text = Section("Как вставлять текст", "буфер обмена, пробелы, «Отправь»")
        text.add_row("Показывать текст во время речи", "Над капсулой видно, что распознаётся.",
                     bind_combo(s, "asr.streaming", [("hands_free", "Когда говорю без удержания"),
                                                     ("always", "Всегда"), ("never", "Никогда")]))
        text.add_row("Пробел после текста", "Чтобы фразы не слипались.", bind_switch(s, "insert.trailing_space"))
        text.add_row("Возвращать буфер обмена", "После вставки в буфере останется то, что было до неё.",
                     bind_switch(s, "insert.restore_clipboard"))
        text.add_row("«Отправь» отправляет сообщение", "Скажите «Отправь.» в конце — нажмётся Enter.",
                     bind_switch(s, "insert.send_it"))
        text.add_row("Убирать «э-э», «мм»", "", bind_switch(s, "text.remove_fillers"))
        text.add_row("Встроенный словарь терминов", "Около 4 тыс. названий: «гитхаб» → GitHub, «докером» → Docker, "
                     "«эс кью эль» → SQL. Ваши слова в «Словаре» важнее.", bind_switch(s, "text.builtin_terms"))
        text.add_row("Числа цифрами", "«номер один» → «№ 1», «двадцать пять» → «25».",
                     bind_switch(s, "text.numbers"))
        text.add_row("Как писать номер", "", bind_combo(s, "text.number_style", [("sign", "№ 5"),
                                                                               ("word", "номер 5")]))
        text.add_row("«Новая строка», «новый абзац»", "Голосом делать переносы.",
                     bind_switch(s, "text.voice_commands"))
        text.add_row("Неформально в мессенджерах", "В Telegram и т.п. — с маленькой буквы и без точки.",
                     bind_switch(s, "text.casual_messaging"))
        text.add_row("Способ вставки", "Если в какой-то программе текст не вставляется — попробуйте «Набор».",
                     bind_combo(s, "insert.method", [("paste", "Вставка (быстро)"), ("type", "Набор по буквам"),
                                                     ("clipboard", "Только скопировать")]))
        adv.addWidget(text)

        ai = Section("ИИ-помощник", "DeepSeek, запасная модель, правка выделенного голосом")
        self.ai_section = ai
        ai.add_row("DeepSeek — основной ИИ", "Быстро, качественно и дёшево; видеокарта не нужна. "
                                             "Нужен интернет и ключ API.", bind_switch(s, "llm.cloud"))
        kw = QWidget()
        krow = QHBoxLayout(kw)
        krow.setContentsMargins(0, 0, 0, 0)
        krow.setSpacing(8)
        self.ds_key = bind_line(s, "llm.deepseek_key", "sk-…", password=True)
        self.ds_key.setMinimumWidth(240)
        self.ds_key.editingFinished.connect(lambda: self._show_ai_state(self.app.local_llm.state,
                                                                        self.app.local_llm.message, -1))
        ds_test = QPushButton("Проверить")
        ds_test.setCursor(Qt.PointingHandCursor)
        ds_test.clicked.connect(self._deepseek_test)
        krow.addWidget(self.ds_key, 1)
        krow.addWidget(ds_test)
        ai.add_row("Ключ DeepSeek API", "platform.deepseek.com → API keys. Хранится только на этом компьютере.", kw)
        self.ds_model = QComboBox()
        self.ds_model.setEditable(True)
        self.ds_model.setMinimumWidth(220)
        self.ds_model.addItems(llm.DEEPSEEK_MODELS)
        self.ds_model.setCurrentText(s.get("llm.deepseek_model") or llm.DEEPSEEK_MODELS[0])
        self.ds_model.currentTextChanged.connect(lambda t: s.set("llm.deepseek_model", t.strip()))
        ai.add_row("Модель DeepSeek", "Flash без «размышлений» — самая быстрая.", self.ds_model)
        self.ds_status = QLabel("")
        self.ds_status.setObjectName("Muted")
        self.ds_status.setWordWrap(True)
        self.ds_status.setContentsMargins(0, 4, 0, 8)
        self.ds_status_row = ai.add_widget(self.ds_status)
        self.ds_status_row.hide()
        ai.add_row("Сколько ждать DeepSeek", "Не ответил — запасная модель или текст без исправления.",
                   bind_spin(s, "llm.cloud_timeout_ms", 1000, 15000, " мс", 500))

        ai.add_row("Запасная модель", "Работает без интернета, если DeepSeek недоступен.",
                   bind_combo(s, "llm.provider", [
                       ("builtin", "Встроенная Qwen3.5-0.8B (llama.cpp)"),
                       ("ollama", "Ollama (например qwen3.5:0.8b-local)"),
                       ("openai", "Другой сервер (OpenAI API)")], on_change=lambda _: self._update_ai_rows()))
        self.ai_rows = {"builtin": [], "ollama": [], "openai": []}
        self.ai_rows["builtin"].append(ai.add_row("Где запускать", "Видеокарта — быстрее всего.", bind_combo(
            s, "llm.builtin_backend", [("auto", "Автоматически"), ("cuda", "Видеокарта (CUDA)"),
                                       ("vulkan", "Видеокарта (Vulkan)"), ("cpu", "Процессор")])))
        bw = QWidget()
        brow = QHBoxLayout(bw)
        brow.setContentsMargins(0, 8, 0, 8)
        restart = QPushButton("Скачать / запустить")
        restart.setCursor(Qt.PointingHandCursor)
        restart.clicked.connect(lambda: (self.app.local_llm.stop(), self.app.install_llm()))
        remove = QPushButton("Удалить модель")
        remove.setObjectName("Danger")
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(self._ai_remove)
        brow.addWidget(restart)
        brow.addWidget(remove)
        brow.addStretch(1)
        self.ai_rows["builtin"].append(ai.add_widget(bw))
        self.ai_rows["ollama"].append(ai.add_row("Адрес Ollama", "", bind_line(s, "llm.ollama_url")))
        ow = QWidget()
        orow = QHBoxLayout(ow)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.setSpacing(8)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setMinimumWidth(220)
        self.ollama_model.setCurrentText(s.get("llm.ollama_model") or "")
        self.ollama_model.currentTextChanged.connect(lambda t: s.set("llm.ollama_model", t.strip()))
        refresh = QPushButton("↻")
        refresh.setToolTip("Список моделей Ollama")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self._ollama_refresh)
        orow.addWidget(self.ollama_model)
        orow.addWidget(refresh)
        self.ai_rows["ollama"].append(ai.add_row("Модель в Ollama", "", ow))
        self.ai_rows["openai"].append(ai.add_row("Адрес сервера", "LM Studio: http://localhost:1234/v1",
                                                 bind_line(s, "llm.base_url")))
        self.ai_rows["openai"].append(ai.add_row("Модель на сервере", "", bind_line(s, "llm.model")))
        tw = QWidget()
        trow = QHBoxLayout(tw)
        trow.setContentsMargins(0, 8, 0, 8)
        test = QPushButton("Проверить запасную")
        test.setCursor(Qt.PointingHandCursor)
        test.clicked.connect(self._llm_test)
        self.llm_status = QLabel("")
        self.llm_status.setObjectName("Muted")
        self.llm_status.setWordWrap(True)
        trow.addWidget(test)
        trow.addWidget(self.llm_status, 1)
        ai.add_widget(tw)
        ai.add_row("Сколько ждать запасную", "Не успела — вставится текст без исправления.",
                   bind_spin(s, "llm.correct_timeout_ms", 500, 8000, " мс", 250))
        ai.add_row("Правка выделенного голосом", "Выделите текст, удерживайте клавишу диктовки и скажите, "
                                                 "что сделать: «сократи», «сделай вежливее», «переведи на английский».",
                   bind_switch(s, "llm.enabled", on_change=self._ai_toggled))
        self.instr_edit = QPlainTextEdit(s.get("llm.instructions") or "")
        self.instr_edit.setPlaceholderText("Свои правила обычными словами, например: «пиши «ё», без канцелярита»")
        self.instr_edit.setFixedHeight(80)
        self.instr_edit.textChanged.connect(lambda: s.set("llm.instructions", self.instr_edit.toPlainText()))
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 10, 0, 10)
        hl.addWidget(self.instr_edit)
        ai.add_widget(holder)
        adv.addWidget(ai)
        self._update_ai_rows()

        look = Section("Внешний вид", "тема, капсула")
        look.add_row("Тема", "", bind_combo(s, "ui.theme", [("auto", "Как в системе"), ("dark", "Тёмная"),
                                                            ("light", "Светлая")],
                                            on_change=lambda _: QTimer.singleShot(0, self.app.rebuild_window)))
        look.add_row("Стекло", "Полупрозрачное окно с размытием (через Blur My Shell).",
                     bind_switch(s, "ui.glass", on_change=self._toggle_glass))
        look.add_row("Вид капсулы", "", bind_combo(s, "bubble.style", [("glass", "Стеклянная с синим шаром"),
                                                                       ("classic", "Чёрная")]))
        look.add_row("Где капсула", "Её можно перетащить мышью.",
                     bind_combo(s, "bubble.position", [("bottom", "Внизу, над доком"), ("top", "Вверху")]))
        look.add_row("Режим демонстрации", "Подпись на капсуле — для записи экрана.",
                     bind_switch(s, "bubble.demo_mode"))
        adv.addWidget(look)

        sound = Section("Звук и микрофон", "громкость, музыка во время записи")
        vol = QSlider(Qt.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(int(float(s.get("audio.sound_volume", 0.35)) * 100))
        vol.setFixedWidth(200)
        vol.valueChanged.connect(lambda v: s.set("audio.sound_volume", v / 100))
        vol.sliderReleased.connect(lambda: self.app.sounds.play("start"))
        sound.add_row("Громкость сигналов", "", vol)
        sound.add_row("Музыка и видео во время записи", "", bind_combo(s, "audio.while_dictating", [
            ("mute", "Выключать звук"), ("pause", "Ставить на паузу"), ("none", "Не трогать")]))
        sound.add_row("Микрофон всегда наготове", "Запись начинается мгновенно, но значок микрофона горит "
                                                  "постоянно.", bind_switch(s, "audio.keep_mic_warm"))
        adv.addWidget(sound)

        privacy = Section("Приватность и история", "что сохраняется")
        privacy.add_row("Не сохранять историю", "Диктовки нигде не запоминаются.",
                        bind_switch(s, "general.privacy_mode"))
        privacy.add_row("Сохранять запись голоса", "Чтобы прослушать позже.", bind_switch(s, "audio.save_audio"))
        privacy.add_row("Хранить записи", "", bind_spin(s, "audio.keep_audio_days", 1, 365, " дн."))
        clear = QPushButton("Очистить")
        clear.setObjectName("Danger")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear_history)
        privacy.add_row("Очистить историю", "Удалить все диктовки и записи голоса.", clear)
        adv.addWidget(privacy)

        engine = Section("Распознавание", "модель и видеокарта")
        self.model_status = QLabel("")
        self.model_status.setWordWrap(True)
        self.model_status.setContentsMargins(0, 10, 0, 4)
        engine.add_widget(self.model_status)
        self.model_path = QLabel("")
        self.model_path.setObjectName("Muted")
        self.model_path.setWordWrap(True)
        self.model_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.model_path.setContentsMargins(0, 6, 0, 6)
        engine.add_widget(self.model_path)
        bw = QWidget()
        brow = QHBoxLayout(bw)
        brow.setContentsMargins(0, 6, 0, 6)
        reload_btn = QPushButton("Перезапустить")
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.clicked.connect(self.app.engine.reload)
        choose = QPushButton("Папка с моделью…")
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self._choose_model_dir)
        data_btn = QPushButton("Папка данных")
        data_btn.setCursor(Qt.PointingHandCursor)
        data_btn.clicked.connect(lambda: subprocess.Popen(["xdg-open", str(DATA_DIR)]))
        brow.addWidget(reload_btn)
        brow.addWidget(choose)
        brow.addWidget(data_btn)
        brow.addStretch(1)
        engine.add_widget(bw)
        engine.add_row("Где считать", "Видеокарта NVIDIA в разы быстрее процессора.", bind_combo(s, "asr.device", [
            ("auto", "Автоматически"), ("cuda", "Видеокарта (CUDA)"), ("cpu", "Процессор")]))
        engine.add_row("Точность", "INT8 — быстрый режим вашей сборки GigaAM.", bind_combo(s, "asr.precision", [
            ("auto", "Автоматически"), ("int8", "INT8"), ("fp16", "FP16"), ("fp32", "FP32")]))
        engine.add_row("Освобождать видеопамять", "Через сколько минут простоя выгружать модель (0 — никогда).",
                       bind_spin(s, "asr.unload_after_min", 0, 240, " мин", 5))
        adv.addWidget(engine)
        lay.addStretch(1)
        return pg

    # ------------------------------------------------------------- ИИ
    def _update_ai_rows(self) -> None:
        provider = self.app.corrector.provider()
        for name, rows in getattr(self, "ai_rows", {}).items():
            for row in rows:
                row.setVisible(name == provider)
        if provider == "ollama" and self.ollama_model.count() == 0:
            self._ollama_refresh(silent=True)
        self._show_ai_state(self.app.local_llm.state, self.app.local_llm.message, -1)

    def _ollama_refresh(self, silent: bool = False) -> None:
        from ..corrector import ollama_models
        models = ollama_models(self.settings.get("llm.ollama_url") or "http://127.0.0.1:11434")
        current = self.settings.get("llm.ollama_model") or ""
        self.ollama_model.blockSignals(True)
        self.ollama_model.clear()
        self.ollama_model.addItems(models)
        self.ollama_model.setCurrentText(current)
        self.ollama_model.blockSignals(False)
        if not silent:
            self.llm_status.setText(f"Моделей в Ollama: {len(models)}" if models else "Ollama не отвечает")

    def _ai_toggled(self, on: bool) -> None:
        if not on or self.app.router.cloud() is not None:
            return      # с ключом DeepSeek ничего скачивать не нужно
        if self.app.corrector.provider() != "builtin":
            return
        local = self.app.local_llm
        if local.ready or local.state in ("installing", "starting"):
            return
        if not local.installed():
            from ..corrector import ollama_models, pick_qwen_small
            found = pick_qwen_small(ollama_models(self.settings.get("llm.ollama_url") or "http://127.0.0.1:11434"))
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText("Какой ИИ использовать?")
            box.setInformativeText("DeepSeek — быстрее и качественнее, нужен ключ API и интернет.\n"
                                   + (f"Ollama — у вас уже есть модель «{found}»." if found else
                                      "Локальная Qwen3.5-0.8B — без интернета, скачается один раз (~0,5 ГБ)."))
            ds = box.addButton("Вставить ключ DeepSeek", QMessageBox.AcceptRole)
            loc = box.addButton(f"Ollama ({found})" if found else "Скачать локальную", QMessageBox.ActionRole)
            box.addButton("Отмена", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is ds:
                self.focus_ai(key=True)
                return
            if clicked is not loc:
                self.ai_switch.setChecked(False)
                return
            if found:
                self.settings.set("llm.ollama_model", found)
                self.settings.set("llm.provider", "ollama")
                self.ollama_model.setCurrentText(found)
                self._update_ai_rows()
                return
        self.app.install_llm()

    def focus_ai(self, key: bool = True) -> None:
        """Открыть раздел «ИИ-помощник» и поставить курсор в поле ключа DeepSeek."""
        self.open_page("settings")
        sec = getattr(self, "ai_section", None)
        if sec is None:
            return
        sec.header.setChecked(True)
        area = self.pages["settings"].area
        QTimer.singleShot(60, lambda: area.ensureWidgetVisible(sec, 0, 40))
        if key:
            QTimer.singleShot(120, self.ds_key.setFocus)

    def _deepseek_test(self) -> None:
        key = self.ds_key.text().strip()
        self.settings.set("llm.deepseek_key", key)
        if not key:
            self._on_ds_test("Вставьте ключ — его выдают на platform.deepseek.com → API keys.", False, None)
            return
        self.ds_status.setText("Проверяю DeepSeek…")
        self.ds_status_row.show()
        self.ds_status.setStyleSheet("")
        settings, router = self.settings, self.app.router

        def work():
            import time as _t
            models = None
            try:
                try:
                    models = llm.list_deepseek_models(key, settings.get("llm.deepseek_url") or llm.DEEPSEEK_URL)
                except llm.LLMError as exc:
                    if getattr(exc, "fatal", False):
                        raise
                provider = router.cloud()
                if provider is None:
                    raise llm.LLMError("DeepSeek выключен переключателем выше")
                t0 = _t.perf_counter()
                answer = llm.request(provider, [{"role": "user", "content": "Ответь одним словом: готово"}],
                                     max_tokens=10, timeout_s=15)
                ms = (_t.perf_counter() - t0) * 1000
                router.reset_cloud()
                self.ds_result.emit(f"✓ DeepSeek работает: ответ за {ms:.0f} мс («{answer[:20]}»).", True, models)
            except Exception as exc:  # noqa: BLE001
                self.ds_result.emit(f"Не получилось: {llm.explain(exc)} ({str(exc)[:120]})", False, models)

        threading.Thread(target=work, daemon=True).start()

    def _on_ds_test(self, text: str, ok: bool, models) -> None:
        self.ds_status.setText(text)
        self.ds_status_row.setVisible(bool(text))
        self.ds_status.setStyleSheet(f"color: {self.colors['success' if ok else 'danger']};")
        if models:
            current = self.ds_model.currentText()
            self.ds_model.blockSignals(True)
            self.ds_model.clear()
            self.ds_model.addItems([m for m in models if "reasoner" not in m] or models)
            self.ds_model.setCurrentText(current if current in models else
                                         next((m for m in llm.DEEPSEEK_MODELS if m in models), models[0]))
            self.ds_model.blockSignals(False)
            self.settings.set("llm.deepseek_model", self.ds_model.currentText())
        self._show_ai_state(self.app.local_llm.state, self.app.local_llm.message, -1)

    def _ai_remove(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Удалить ИИ-модель и движок с диска?") == QMessageBox.Yes:
            self.settings.set("llm.correct", False)
            self.settings.set("llm.enabled", False)
            self.ai_switch.setChecked(False)
            threading.Thread(target=self.app.local_llm.remove, daemon=True).start()

    def _show_ai_state(self, state: str, message: str, progress: float) -> None:
        if not hasattr(self, "ai_status"):
            return
        provider = self.app.corrector.provider()
        builtin = provider == "builtin"
        enabled = self.settings.get("llm.correct", False) or self.settings.get("llm.enabled", False)
        cloud = self.app.router.cloud()
        if cloud is not None:
            local = {"builtin": "Qwen3.5 (локально)", "ollama": f"Ollama · {self.settings.get('llm.ollama_model')}",
                     "openai": "свой сервер"}.get(provider, provider)
            text = f"✓ DeepSeek · {cloud.model}. Запасная: {local}." if enabled else ""
            if self.app.router.last_cloud_error and enabled:
                text = f"DeepSeek: {self.app.router.last_cloud_error}. Сейчас работает запасная: {local}."
            if state == "installing" and progress >= 0:
                text += f" {message}"
            else:
                state = "ready"
        else:
            text = {"ready": f"✓ Готово · {message}", "error": message,
                    "stopped": "Модель скачана — включится вместе с переключателем.",
                    "absent": "", "cloud": ""}.get(state, message)
            if provider == "ollama":
                text = f"✓ Ollama · {self.settings.get('llm.ollama_model')}" if enabled else ""
                state = "ready"
            elif provider == "openai":
                text = "Используется ваш сервер (Дополнительно → ИИ-помощник)." if enabled else ""
                state = "ready"
            elif not enabled and state in ("stopped", "absent"):
                text = ""
            elif enabled and state == "absent":
                text = "Вставьте ключ DeepSeek (Дополнительно → ИИ-помощник) или скачайте локальную модель."
        self.ai_status.setText(text)
        self.ai_status.setStyleSheet(f"color: {self.colors['danger']};" if state == "error" else "")
        self.ai_progress.setVisible(builtin and state == "installing" and progress >= 0)
        if progress >= 0:
            self.ai_progress.setValue(int(progress * 1000))
        self.ai_box.setVisible(bool(text) or self.ai_progress.isVisible())

    # ------------------------------------------------------------- уведомления
    def _render_notices(self) -> None:
        if not hasattr(self, "notice_lay"):
            return
        while self.notice_lay.count():
            item = self.notice_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key, (kind, text, action) in list(self.app.notices.items()):
            card = QFrame()
            card.setObjectName("BannerError" if kind == "error" else "Banner")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 10, 10, 10)
            row.setSpacing(10)
            label = QLabel(("⚠ " if kind in ("warn", "error") else "") + text)
            label.setWordWrap(True)
            label.setStyleSheet("background: transparent;")
            row.addWidget(label, 1)
            if action:
                btn = QPushButton(action)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setObjectName("Primary")
                btn.clicked.connect(lambda _=False, k=key: self.app.notice_action(k))
                row.addWidget(btn, 0, Qt.AlignVCenter)
            close = QPushButton("×")
            close.setFixedSize(28, 28)
            close.setStyleSheet("padding: 0; font-size: 16px;")
            close.setCursor(Qt.PointingHandCursor)
            close.setToolTip("Скрыть")
            close.clicked.connect(lambda _=False, k=key: self.app.clear_notice(k))
            row.addWidget(close, 0, Qt.AlignVCenter)
            self.notice_lay.addWidget(card)
        self.notice_holder.setVisible(bool(self.app.notices))

    def _clear_history(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Удалить всю историю и записи голоса?") == QMessageBox.Yes:
            self.app.history.clear()
            self.refresh_history_views()

    def _toggle_glass(self, on: bool) -> None:
        if desktop.blur_my_shell_installed():
            desktop.enable_blur_for_app(on)
        QTimer.singleShot(0, self.app.rebuild_window)

    def _autostart(self, enabled: bool) -> None:
        from ..autostart import set_autostart
        set_autostart(enabled)

    def _fill_mics(self) -> None:
        current = self.settings.get("audio.input_device")
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for value, label in list_input_devices():
            self.mic_combo.addItem(label, value)
        found = self.mic_combo.findData(current)
        self.mic_combo.setCurrentIndex(found if found >= 0 else 0)
        self.mic_combo.blockSignals(False)

    def _llm_test(self) -> None:
        self.llm_status.setText("Проверяю…")
        router = self.app.router

        def work():
            local = router.local()
            if local is None:
                self.llm_result.emit("Запасная модель не запущена: включите ИИ или нажмите «Скачать / запустить».",
                                     False)
                return
            try:
                answer = llm.request(local, [{"role": "user", "content": "Скажи одно слово: готово"}],
                                     max_tokens=16, timeout_s=20)
                self.llm_result.emit(f"✓ Работает. Ответ: {answer[:40]}", True)
            except Exception as exc:  # noqa: BLE001
                self.llm_result.emit(f"Не удалось подключиться: {exc}", False)

        threading.Thread(target=work, daemon=True).start()

    def _on_llm_test(self, text: str, ok: bool) -> None:
        self.llm_status.setText(text)
        self.llm_status.setStyleSheet(f"color: {self.colors['success' if ok else 'danger']};")

    def _choose_model_dir(self) -> None:
        start = self.settings.get("asr.model_dir") or str(MODELS_DIR)
        path = QFileDialog.getExistingDirectory(self, "Папка с v3_e2e_rnnt.ckpt", start)
        if path:
            self.settings.set("asr.model_dir", path)
            self._update_model_path()

    def _update_model_path(self) -> None:
        found = find_model_dir(self.settings)
        self.model_path.setText(f"Модель GigaAM v3: {found}" if found else
                                f"Модель не найдена — скачается в {MODELS_DIR} при первом запуске (~0,9 ГБ).")

    # ------------------------------------------------------------- состояние
    def _on_status(self, state: str, message: str) -> None:
        friendly = {"loading": "Готовлю распознавание — несколько секунд…",
                    "downloading": "Скачиваю модель распознавания (один раз, ~0,9 ГБ)…",
                    "error": "Распознавание не запустилось. Подробности: Настройки → Распознавание.",
                    "unloaded": "", "ready": "",
                    "mic-error": "Не получилось включить микрофон. Проверьте, что он подключён, "
                                 "и выберите его в Настройках."}.get(state, "")
        self.banner.setText(friendly)
        self.banner.setObjectName("BannerError" if state in ("error", "mic-error") else "Banner")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner.setVisible(bool(friendly))
        if state == "mic-error":
            QTimer.singleShot(8000, self.banner.hide)
        self.side_status.setText({"loading": "Готовлю распознавание…", "downloading": "Скачиваю модель…",
                                  "error": "⚠ Ошибка распознавания",
                                  "degraded": "⚠ Видеопамять занята"}.get(state, ""))
        color = {"ready": self.colors["success"], "error": self.colors["danger"]}.get(state, self.colors["warn"])
        label = {"ready": "Работает", "loading": "Загружается", "downloading": "Скачивается", "error": "Ошибка",
                 "unloaded": "Выгружена (загрузится при диктовке)",
                 "degraded": "Работает на процессоре — видеопамять занята"}.get(state, state)
        self.model_status.setText(f"<span style='color:{color}'>●</span>&nbsp; <b>{label}</b><br>"
                                  f"<span style='color:{self.colors['muted']}'>{message}</span>")
        self._update_model_path()
