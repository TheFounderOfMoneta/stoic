"""Мастер первого запуска: знакомство → микрофон → первая диктовка → пара приёмов."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..audio import list_input_devices
from ..hotkeys import pretty_combo
from .widgets import LevelMeter, OrbWidget, keycaps_row


def _step_row(num: int, widget_or_text) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    n = QLabel(str(num))
    n.setObjectName("StepNum")
    n.setAlignment(Qt.AlignCenter)
    lay.addWidget(n, 0, Qt.AlignTop)
    if isinstance(widget_or_text, str):
        t = QLabel(widget_or_text)
        t.setObjectName("Step")
        t.setWordWrap(True)
        lay.addWidget(t, 1)
    else:
        lay.addWidget(widget_or_text, 1)
    return w


class Welcome(QFrame):
    """Полноэкранная (внутри окна) карточка-мастер. Показывается один раз; повтор — «Как пользоваться»."""

    finished = Signal()
    ai_result = Signal(bool, str)

    def __init__(self, window, host: QWidget):
        super().__init__(host)
        self.window = window
        self.app = window.app
        self.setObjectName("Welcome")
        self.setAttribute(Qt.WA_StyledBackground, True)
        host.installEventFilter(self)
        self.host = host
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 28)
        top = QHBoxLayout()
        self.dots = QLabel()
        self.dots.setObjectName("Muted")
        top.addWidget(self.dots)
        top.addStretch(1)
        skip = QPushButton("Пропустить")
        skip.setObjectName("Link")
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self.finish)
        top.addWidget(skip)
        outer.addLayout(top)

        center = QHBoxLayout()
        center.addStretch(1)
        self.stack = QStackedWidget()
        self.stack.setMaximumWidth(620)
        self.stack.setMinimumWidth(480)
        center.addWidget(self.stack, 10)
        center.addStretch(1)
        outer.addStretch(1)
        outer.addLayout(center)
        outer.addStretch(1)

        nav = QHBoxLayout()
        nav.addStretch(1)
        self.back = QPushButton("Назад")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next = QPushButton("Начать")
        self.next.setObjectName("Primary")
        self.next.setCursor(Qt.PointingHandCursor)
        self.next.setMinimumWidth(140)
        self.next.clicked.connect(lambda: self.go(self.stack.currentIndex() + 1))
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        nav.addStretch(1)
        outer.addLayout(nav)

        for build in (self._hello, self._mic, self._try, self._ai, self._tricks):
            self.stack.addWidget(build())
        self.app.mic_level.connect(self._on_level)
        self.ai_result.connect(self._ai_done)
        self._heard = 0
        self.setGeometry(host.rect())
        self.show()
        self.raise_()
        self.go(0)

    # ------------------------------------------------------------- шаги
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        return w, lay

    def _title(self, lay: QVBoxLayout, title: str, text: str = "") -> None:
        t = QLabel(title)
        t.setObjectName("WelcomeTitle")
        t.setWordWrap(True)
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        if text:
            s = QLabel(text)
            s.setObjectName("WelcomeText")
            s.setWordWrap(True)
            s.setAlignment(Qt.AlignCenter)
            lay.addWidget(s)

    def _activate_combo(self) -> list:
        combos = self.app.settings.get("hotkeys.activate") or []
        return combos[0] if combos else ["ralt"]

    def _hello(self) -> QWidget:
        w, lay = self._page()
        orb = OrbWidget(132)
        lay.addWidget(orb, 0, Qt.AlignHCenter)
        self._title(lay, "Добро пожаловать в Aqua",
                    "Говорите — и текст сам появится там, где стоит курсор: в браузере, мессенджере, "
                    "документе. Распознавание работает прямо на вашем компьютере, без интернета.")
        return w

    def _mic(self) -> QWidget:
        w, lay = self._page()
        self._title(lay, "Проверим микрофон", "Скажите что-нибудь вслух — полоска должна заполняться.")
        lay.addSpacing(8)
        self.mic_combo = QComboBox()
        for value, label in list_input_devices():
            self.mic_combo.addItem(label, value)
        found = self.mic_combo.findData(self.app.settings.get("audio.input_device"))
        self.mic_combo.setCurrentIndex(found if found >= 0 else 0)
        self.mic_combo.currentIndexChanged.connect(self._mic_changed)
        lay.addWidget(self.mic_combo)
        self.meter = LevelMeter()
        self.meter.setMinimumHeight(18)
        lay.addWidget(self.meter)
        self.heard = QLabel("Слушаю…")
        self.heard.setObjectName("WelcomeText")
        self.heard.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.heard)
        return w

    def _try(self) -> QWidget:
        w, lay = self._page()
        self._title(lay, "Попробуйте")
        combo = self._activate_combo()
        lay.addWidget(_step_row(1, "Щёлкните в поле ниже."))
        lay.addWidget(_step_row(2, keycaps_row(combo, "удерживайте и скажите: «Привет, это мой первый текст»")))
        lay.addWidget(_step_row(3, "Отпустите клавишу — текст появится в поле."))
        self.try_box = QPlainTextEdit()
        self.try_box.setPlaceholderText("Здесь появится ваш текст…")
        self.try_box.setFixedHeight(96)
        self.try_box.textChanged.connect(self._tried)
        lay.addWidget(self.try_box)
        self.try_status = QLabel("")
        self.try_status.setObjectName("WelcomeText")
        self.try_status.setAlignment(Qt.AlignCenter)
        self.try_status.setWordWrap(True)
        lay.addWidget(self.try_status)
        return w

    def _ai(self) -> QWidget:
        """Необязательный шаг: ключ DeepSeek — ИИ исправляет ошибки и подставляет термины."""
        from PySide6.QtWidgets import QLineEdit
        w, lay = self._page()
        self._title(lay, "Улучшение текста ИИ",
                    "Необязательно. С ключом DeepSeek API текст исправляется по смыслу: термины (GitHub, Docker), "
                    "запятые, оговорки. Стоит копейки, видеокарта не нужна. Ключ: platform.deepseek.com → API keys.")
        self.ds_key = QLineEdit(self.app.settings.get("llm.deepseek_key") or "")
        self.ds_key.setEchoMode(QLineEdit.Password)
        self.ds_key.setPlaceholderText("sk-…   (можно пропустить и вставить позже в Настройках)")
        lay.addWidget(self.ds_key)
        row = QHBoxLayout()
        check = QPushButton("Проверить и включить")
        check.setCursor(Qt.PointingHandCursor)
        check.clicked.connect(self._ai_check)
        row.addWidget(check)
        row.addStretch(1)
        lay.addLayout(row)
        self.ai_status = QLabel("")
        self.ai_status.setObjectName("WelcomeText")
        self.ai_status.setWordWrap(True)
        self.ai_status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.ai_status)
        return w

    def _ai_check(self) -> None:
        import threading
        from .. import llm
        key = self.ds_key.text().strip()
        if not key:
            self.ai_status.setText("Без ключа всё работает: GigaAM сам ставит пунктуацию. ИИ можно включить позже.")
            return
        settings = self.app.settings
        settings.set("llm.deepseek_key", key)
        settings.set("llm.cloud", True)
        self.ai_status.setText("Проверяю…")

        def work():
            try:
                provider = self.app.router.cloud()
                llm.request(provider, [{"role": "user", "content": "Ответь одним словом: готово"}],
                            max_tokens=10, timeout_s=15)
                ok, text = True, "✓  DeepSeek работает — улучшение текста включено."
            except Exception as exc:  # noqa: BLE001
                ok, text = False, f"Не получилось: {llm.explain(exc)}. Ключ сохранён — проверьте его позже в Настройках."
            self.ai_result.emit(ok, text)      # сигнал доставит результат в поток интерфейса

        threading.Thread(target=work, daemon=True).start()

    def _ai_done(self, ok: bool, text: str) -> None:
        if ok:
            self.app.settings.set("llm.correct", True)
            self.app.settings.set("llm.enabled", True)
        self.ai_status.setText(text)

    def _tricks(self) -> QWidget:
        w, lay = self._page()
        self._title(lay, "Ещё три приёма")
        combo = self._activate_combo()
        name = pretty_combo(combo)
        lay.addWidget(_step_row(1, f"Коротко нажмите {name} (или щёлкните по капсуле) — можно долго говорить, "
                                   f"не держа клавишу. Чтобы закончить, нажмите {name} ещё раз."))
        lay.addWidget(_step_row(2, "Передумали во время записи — нажмите Esc, ничего не вставится."))
        lay.addWidget(_step_row(3, f"Выделите текст и удерживайте {name} — скажите, что поменять: "
                                   "«сделай вежливее», «переведи на английский». Для этого нужен ИИ-помощник "
                                   "(Настройки → Дополнительно)."))
        tip = QLabel("Капсула над доком показывает, что Aqua слушает. Значок Aqua есть и в верхней панели.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(tip)
        return w

    # ------------------------------------------------------------- логика
    def go(self, index: int) -> None:
        if index >= self.stack.count():
            self.finish()
            return
        index = max(0, index)
        self.stack.setCurrentIndex(index)
        self.back.setVisible(index > 0)
        self.next.setText({0: "Начать", self.stack.count() - 1: "Готово"}.get(index, "Далее"))
        self.dots.setText("  ".join("●" if i == index else "○" for i in range(self.stack.count())))
        self.app.mic_test(index == 1)
        if index == 2:
            self._update_try_status()
            QTimer.singleShot(50, self.try_box.setFocus)

    def _mic_changed(self, i: int) -> None:
        self.app.settings.set("audio.input_device", self.mic_combo.itemData(i))
        self.app.mic_test(False)
        self.app.mic_test(True)
        self._heard = 0

    def _on_level(self, level: float) -> None:
        if self.stack.currentIndex() != 1:
            return
        self.meter.set_level(level)
        if level > 0.3:
            self._heard += 1
        if self._heard > 6:
            self.heard.setText("✓  Слышу вас — микрофон работает")

    def _update_try_status(self) -> None:
        state = self.app.engine_state[0]
        if self.try_box.toPlainText().strip():
            self.try_status.setText("✓  Получилось! Так же это работает в любой программе.")
        elif state in ("loading", "downloading"):
            self.try_status.setText("Распознавание ещё готовится — это несколько секунд при первом запуске…")
            QTimer.singleShot(1000, self._update_try_status)
        elif state == "error":
            self.try_status.setText("Распознавание не запустилось — загляните в Настройки → Распознавание.")
        else:
            self.try_status.setText("")

    def _tried(self) -> None:
        self._update_try_status()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.host and event.type() == event.Type.Resize:
            self.setGeometry(self.host.rect())
        return False

    def finish(self) -> None:
        self.app.mic_test(False)
        try:
            self.app.mic_level.disconnect(self._on_level)
        except (RuntimeError, TypeError):
            pass
        self.host.removeEventFilter(self)
        self.app.settings.set("ui.welcome_done", True)
        self.hide()
        self.deleteLater()
        self.finished.emit()
