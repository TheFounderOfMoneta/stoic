"""Первый запуск: четыре коротких шага (как мастер Aqua и «Сводки»). Всё можно поменять в Настройках.

1. Что это и как система подстраивается.
2. Claude: установлен ли, выполнен ли вход (кнопки, которые это исправляют).
3. О вас: имя и интересы — из них Claude берёт примеры, крючки и подарки.
4. Напоминание и первая тема.
"""
from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QTimeEdit,
                               QVBoxLayout, QWidget)

from ..config import APP_NAME
from . import widgets as W
from .look import app_icon_pixmap


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
        self.resize(680, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 22)
        lay.setSpacing(14)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pages = [self._intro(), self._claude(), self._about(), self._reminder()]
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
            self.c.settings.set("profile.name", self.name.text().strip())
            self.c.settings.set("profile.interests", self.interests.text().strip())
        if i < len(self.pages) - 1:
            self.go(i + 1)
            return
        self.c.settings.set("reminder.time", self.time.time().toString("HH:mm"))
        self.c.settings.set("reminder.enabled", self.remind.isChecked())
        self.c.settings.set("ui.welcome_done", True)
        self.c.apply_timer()
        self.accept()
        if self.first_topic.isChecked():
            self.c.new_topic_dialog()

    # ------------------------------------------------------------- шаги
    def _page(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("WelcomeTitle")
        t.setWordWrap(True)
        lay.addWidget(t)
        return w, lay

    def _intro(self) -> QWidget:
        w, lay = self._page(f"{APP_NAME}")
        icon = QLabel()
        icon.setPixmap(app_icon_pixmap(128).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.insertWidget(0, icon)
        lay.addWidget(_label("Учёба с Claude, которая подстраивается под вас, — как рекомендации YouTube, только "
                             "цель не время в приложении, а то, что вы запомнили через неделю.", "WelcomeText"))
        for n, text in ((1, "Каждая сессия — по шаблону: крючок-вопрос, главное сразу, практика, схема по памяти, "
                            "подарок в конце и вопрос на завтра."),
                        (2, "Форматы подачи проверяются на вас: какой порядок и какое закрепление дают лучший "
                            "результат через несколько дней."),
                        (3, "Повторения приходят, когда вы вот-вот забудете. Время, усталость и пропуски система "
                            "учитывает сама."),
                        (4, "Отдельно — «Разговор»: можно выговориться. Без оценок и серий, переписка не хранится.")):
            row = QHBoxLayout()
            num = QLabel(str(n))
            num.setObjectName("StepNum")
            num.setAlignment(Qt.AlignCenter)
            row.addWidget(num, 0, Qt.AlignTop)
            row.addWidget(_label(text, "Step"), 1)
            lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _claude(self) -> QWidget:
        w, lay = self._page("Claude")
        lay.addWidget(_label("Наставник работает через Claude Code по вашей подписке — ключ API не нужен. "
                             "Нужно один раз войти тем же аккаунтом, что и на claude.ai.", "WelcomeText"))
        self.claude_label = _label("Проверяю…")
        lay.addWidget(self.claude_label)
        row = QHBoxLayout()
        self.claude_btn = QPushButton("Войти в Claude")
        self.claude_btn.setObjectName("Primary")
        self.claude_btn.setCursor(Qt.PointingHandCursor)
        self.claude_btn.clicked.connect(self._claude_action)
        recheck = QPushButton("Проверить ещё раз")
        recheck.setCursor(Qt.PointingHandCursor)
        recheck.clicked.connect(lambda: self.c.check_claude(quiet=False))
        row.addWidget(self.claude_btn)
        row.addWidget(recheck)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_label("Можно пропустить и войти позже — тогда сессии начнутся после входа.", "Muted"))
        lay.addStretch(1)
        return w

    def _claude_state(self) -> None:
        st = self.c.claude_status
        if st.get("ok"):
            self.claude_label.setText("✓  Вход выполнен. Всё готово.")
            self.claude_btn.hide()
        elif not st.get("installed", True):
            self.claude_label.setText(st.get("message", ""))
            self.claude_btn.setText("Установить Claude Code")
            self.claude_btn.show()
        else:
            self.claude_label.setText(st.get("message") or "Проверяю…")
            self.claude_btn.setText("Войти в Claude")
            self.claude_btn.setVisible(st.get("checked", False))

    def _claude_action(self) -> None:
        if not self.c.claude_status.get("installed", True):
            self.c.install_claude()
        else:
            self.c.login_claude()

    def _about(self) -> QWidget:
        w, lay = self._page("О вас")
        lay.addWidget(_label("Из интересов Claude берёт примеры, крючки и «подарки» в конце сессий. "
                             "Персонализация по интересам — одна из немногих, что подтверждена исследованиями.",
                             "WelcomeText"))
        lay.addWidget(_label("Как к вам обращаться", "Muted"))
        self.name = QLineEdit(self.c.settings.get("profile.name", ""))
        self.name.setPlaceholderText("Имя (необязательно)")
        lay.addWidget(self.name)
        lay.addWidget(_label("Интересы — через запятую", "Muted"))
        self.interests = QLineEdit(self.c.settings.get("profile.interests", ""))
        self.interests.setPlaceholderText("Например: строить системы и схемы, игры, свой проект, музыка")
        lay.addWidget(self.interests)
        lay.addStretch(1)
        return w

    def _reminder(self) -> QWidget:
        w, lay = self._page("Напоминание")
        lay.addWidget(_label("Одно напоминание в день — только если сегодня ещё не занимались. В нём вопрос, на "
                             "котором вы остановились: с него удобно начать.", "WelcomeText"))
        row = QHBoxLayout()
        self.remind = W.Switch()
        self.remind.setChecked(bool(self.c.settings.get("reminder.enabled", True)))
        row.addWidget(self.remind)
        row.addWidget(_label("Напоминать в", "Soft"))
        self.time = QTimeEdit(QTime.fromString(self.c.settings.get("reminder.time", "19:07"), "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        self.time.setFixedWidth(100)
        row.addWidget(self.time)
        row.addStretch(1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.first_topic = W.Switch()
        self.first_topic.setChecked(True)
        row2.addWidget(self.first_topic)
        row2.addWidget(_label("Сразу создать первую тему", "Soft"))
        row2.addStretch(1)
        lay.addLayout(row2)
        lay.addStretch(1)
        return w
