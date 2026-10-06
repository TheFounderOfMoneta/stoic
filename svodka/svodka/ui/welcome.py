"""Первый запуск: четыре коротких шага (как мастер Aqua). Всё можно поменять потом в Настройках.

1. Что это и как учится лента.
2. Claude: установлен ли, выполнен ли вход (кнопки, которые это исправляют).
3. Интересы: темы с весом «Реже / Обычно / Чаще» и профиль своими словами.
4. Расписание, автозапуск и первый сбор.
"""
from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget, QFrame)

from ..config import APP_NAME
from . import widgets as W
from .look import app_icon_pixmap

WEIGHT_STEPS = [(0.5, "Реже"), (1.0, "Обычно"), (1.5, "Чаще")]


def _label(text: str, name: str = "Soft") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(True)
    return lab


class Welcome(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setObjectName("Dialog")
        self.setWindowTitle(f"Добро пожаловать в «{APP_NAME}»")
        self.setModal(True)
        self.resize(700, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 22)
        lay.setSpacing(14)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pages = [self._intro(), self._claude(), self._interests(), self._schedule()]
        for p in self.pages:
            self.stack.addWidget(p)
        foot = QHBoxLayout()
        self.counter = QLabel("")
        self.counter.setObjectName("Muted")
        foot.addWidget(self.counter)
        foot.addStretch(1)
        self.back = QPushButton("Назад")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next = QPushButton("Далее")
        self.next.setObjectName("Primary")
        self.next.setCursor(Qt.PointingHandCursor)
        self.next.setDefault(True)
        self.next.clicked.connect(self._next)
        foot.addWidget(self.back)
        foot.addWidget(self.next)
        lay.addLayout(foot)
        self.c.claude_changed.connect(self._claude_state)
        self.go(0)

    def done(self, result: int) -> None:
        try:
            self.c.claude_changed.disconnect(self._claude_state)
        except (RuntimeError, TypeError):
            pass
        super().done(result)

    # ------------------------------------------------------------- навигация
    def go(self, i: int) -> None:
        i = max(0, min(len(self.pages) - 1, i))
        self.stack.setCurrentIndex(i)
        self.counter.setText(f"Шаг {i + 1} из {len(self.pages)}")
        self.back.setVisible(i > 0)
        self.next.setText("Начать" if i == len(self.pages) - 1 else "Далее")
        if i == 1:
            self._claude_state()
            if not self.c.claude_status.get("checked"):
                self.c.check_claude(quiet=True)

    def _next(self) -> None:
        i = self.stack.currentIndex()
        if i == 2:
            self._save_interests()
        if i < len(self.pages) - 1:
            self.go(i + 1)
        else:
            self._finish()

    def _page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        t.setWordWrap(True)
        lay.addWidget(t)
        if subtitle:
            lay.addWidget(_label(subtitle, "PageSubtitle"))
        return page, lay

    # ------------------------------------------------------------- 1. знакомство
    def _intro(self) -> QWidget:
        page, lay = self._page(f"Добро пожаловать в «{APP_NAME}»", "Личная лента новостей, которая учится на вас")
        logo = QLabel()
        logo.setPixmap(app_icon_pixmap(256).scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(logo)
        for title, text in (
                ("Claude собирает сам", "Утром и вечером Claude ищет свежее по вашим темам, читает статьи и "
                                        "отбирает лучшее. Если ПК был выключен — догонит после включения."),
                ("Читаете по-русски", "Полный перевод статьи прямо в приложении, оригинал абзаца — в один клик."),
                ("Лента подстраивается под вас", "Как рекомендации YouTube: учитывает, что вы открываете, "
                                                 "сколько читаете, что дочитываете, отмечаете и пропускаете. "
                                                 "И всегда может объяснить, почему статья здесь.")):
            card = W.Card(title, "", padding=14)
            card.add(_label(text))
            lay.addWidget(card)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------- 2. Claude
    def _claude(self) -> QWidget:
        page, lay = self._page("Claude", "Работает по вашей подписке через Claude Code — ключи API и своя модель "
                                         "не нужны")
        card = W.Card("", "", padding=16)
        self.claude_label = _label("Проверяю…", "CardTitle")
        self.claude_hint = _label("", "Muted")
        card.add(self.claude_label)
        card.add(self.claude_hint)
        row = QHBoxLayout()
        self.install_btn = QPushButton("Установить Claude Code")
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self.c.show_install_help)
        self.login_btn = QPushButton("Войти в Claude")
        self.login_btn.setObjectName("Primary")
        self.login_btn.clicked.connect(self.c.login_claude)
        check = QPushButton("Проверить снова")
        check.clicked.connect(lambda: self.c.check_claude(quiet=True))
        for b in (self.install_btn, self.login_btn, check):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        card.add_layout(row)
        lay.addWidget(card)
        lay.addWidget(_label("Один сбор — это 3–6 минут работы Claude: поиск, чтение и отбор 10–20 статей. "
                             "Он расходует часть лимита подписки, поэтому по умолчанию сборов два в день, а "
                             "перевод делается заранее только для лучших статей.", "Muted"))
        lay.addStretch(1)
        return page

    def _claude_state(self) -> None:
        st = self.c.claude_status
        if st.get("ok"):
            self.claude_label.setText("✓  Вход выполнен")
            self.claude_hint.setText("Всё готово — можно идти дальше.")
        elif not st.get("installed", True):
            self.claude_label.setText("Claude Code не установлен")
            self.claude_hint.setText("Нажмите «Установить» — откроется терминал с установкой и входом.")
        elif not st.get("checked"):
            self.claude_label.setText("Проверяю…")
            self.claude_hint.setText("")
        else:
            self.claude_label.setText("Нужно войти в Claude")
            self.claude_hint.setText("Откроется терминал и браузер — войдите тем же аккаунтом, что и на claude.ai.")
        self.install_btn.setVisible(not st.get("installed", True))
        self.login_btn.setVisible(st.get("installed", True) and not st.get("ok") and bool(st.get("checked")))

    # ------------------------------------------------------------- 3. интересы
    def _interests(self) -> QWidget:
        page, lay = self._page("Интересы", "Это только отправная точка — дальше лента учится на том, что вы читаете")
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 8, 0)
        il.setSpacing(14)
        self.topic_group = W.Group("Темы")
        self.topic_controls = []
        for t in self.c.storage.topics(enabled_only=False):
            self._add_topic_row(t)
        il.addWidget(self.topic_group)
        add = QHBoxLayout()
        self.new_topic = QLineEdit()
        self.new_topic.setPlaceholderText("Добавить тему, например: Энергетика и сети")
        self.new_topic.returnPressed.connect(self._add_topic)
        b = QPushButton("Добавить")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self._add_topic)
        add.addWidget(self.new_topic, 1)
        add.addWidget(b)
        il.addLayout(add)
        il.addWidget(_label("О себе своими словами — Claude читает это при каждом сборе:", "Muted"))
        self.profile = QPlainTextEdit(self.c.storage.profile_text())
        self.profile.setMinimumHeight(120)
        il.addWidget(self.profile)
        il.addWidget(_label("Позже можно импортировать интересы из истории YouTube и поиска Google "
                            "(Настройки → Обучение ленты).", "Muted"))
        il.addStretch(1)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        return page

    def _add_topic_row(self, t: dict) -> None:
        sw = W.Switch()
        sw.setChecked(bool(t["enabled"]))
        seg = W.Segmented([label for _w, label in WEIGHT_STEPS])
        idx = min(range(3), key=lambda i: abs(WEIGHT_STEPS[i][0] - float(t["weight"])))
        seg.buttons[idx].setChecked(True)
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        cl.addWidget(seg)
        cl.addWidget(sw)
        self.topic_group.add_row(t["name"], t.get("description", ""), controls)
        self.topic_controls.append((t, seg, sw))

    def _add_topic(self) -> None:
        name = self.new_topic.text().strip()
        if not name:
            return
        self.c.storage.upsert_topic(name, "", 1.0)
        t = next((x for x in self.c.storage.topics(enabled_only=False) if x["name"] == name), None)
        if t:
            self._add_topic_row(t)
        self.new_topic.clear()

    def _save_interests(self) -> None:
        st = self.c.storage
        for t, seg, sw in self.topic_controls:
            i = next((k for k, b in enumerate(seg.buttons) if b.isChecked()), 1)
            st.upsert_topic(t["name"], t.get("description", ""), WEIGHT_STEPS[i][0], t.get("include_words", ""),
                            t.get("exclude_words", ""), sw.isChecked())
        text = self.profile.toPlainText().strip()
        if text and text != st.profile_text().strip():
            st.set_profile(text, note="мастер первого запуска")

    # ------------------------------------------------------------- 4. расписание
    def _schedule(self) -> QWidget:
        page, lay = self._page("Расписание", "Лента обновляется сама — окно для этого держать открытым не нужно")
        g = W.Group("Сбор")
        self.sched = W.Switch()
        self.sched.setChecked(bool(self.c.settings.get("schedule.enabled", True)))
        g.add_row("Собирать по расписанию", "Если ПК был выключен — сразу после включения", self.sched)
        times = QWidget()
        tl = QHBoxLayout(times)
        tl.setContentsMargins(0, 0, 0, 0)
        self.time_edits = []
        for t in (self.c.settings.get("schedule.times") or ["07:37", "18:37"])[:2]:
            te = QTimeEdit(QTime.fromString(t, "HH:mm"))
            te.setDisplayFormat("HH:mm")
            te.setFixedWidth(80)
            self.time_edits.append(te)
            tl.addWidget(te)
        g.add_row("Утром и вечером", "Лучше не ровный час — так меньше задержек", times)
        self.autostart = W.Switch()
        self.autostart.setChecked(bool(self.c.settings.get("ui.autostart", True)))
        g.add_row("Запускать при входе в систему", "Значок в верхней панели и уведомления о свежем", self.autostart)
        lay.addWidget(g)
        self.first = QCheckBox("Собрать первую ленту сейчас (3–6 минут)")
        self.first.setChecked(True)
        lay.addWidget(self.first)
        lay.addWidget(_label("Пока идёт сбор, можно заглянуть в Настройки. Первые дни лента чаще показывает "
                             "«Разведку» — новое для вас; отмечайте «Интересно» и «Не моё», так она учится "
                             "быстрее.", "Muted"))
        lay.addStretch(1)
        return page

    def _finish(self) -> None:
        s = self.c.settings
        s.set("schedule.enabled", self.sched.isChecked())
        s.set("schedule.times", sorted({e.time().toString("HH:mm") for e in self.time_edits}))
        s.set("ui.autostart", self.autostart.isChecked())
        s.set("ui.welcome_done", True)
        self.c.reinstall_timer()
        self.c.set_autostart(self.autostart.isChecked())
        first = self.first.isChecked()
        self.accept()
        self.c.update_status()
        if self.c.window is not None:
            self.c.window.open_page("feed")
        if first:
            self.c.collect_now()
