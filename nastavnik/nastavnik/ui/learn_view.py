"""Учёба: список тем, страница темы с картой и сессия с Claude во всё окно.

Сессия идёт по шаблону «крючок → ядро → практика → по памяти → подарок → петля»; шаг сверху
подсвечивается по мере того, как Claude его отмечает. Уверенность в ответе — кнопками над полем
ввода (Alt+1…4): из неё и времени ответа приложение понимает, где вы «знаете», а где угадываете.

Доска (Ctrl+D) открывается справа от чата: задание Claude — сверху, схема рисуется от руки и уходит
Claude в Mermaid. Когда Claude просит нарисовать схему, кнопка «Доска» подсвечивается сама.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..learn import bandit, engine
from ..tutor import LEVELS, STEP_LABELS
from ..util import when_text
from . import widgets as W
from .board import BoardPanel
from .chat import ChatInput, ChatView
from .concept_map import ConceptMap
from .pages import MASTERY_TIP, Page, button, clear_layout, label, progress_text, topic_bar


class NewTopicDialog(QDialog):
    """Новая тема: что, зачем, уровень, заметки. Карту составит Claude."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle("Новая тема")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(12)
        lay.addWidget(label("Что хотите изучить?", "DialogTitle"))
        self.title = QLineEdit()
        self.title.setPlaceholderText("Например: компьютерные сети, теория игр, Rust")
        lay.addWidget(self.title)
        lay.addWidget(label("Зачем — чего хотите добиться", "Muted"))
        self.goal = QLineEdit()
        self.goal.setPlaceholderText("Например: понимать, почему тормозит мой сервер")
        lay.addWidget(self.goal)
        lay.addWidget(label("Уровень", "Muted"))
        self.level = W.Segmented(list(LEVELS.values()))
        lay.addWidget(self.level)
        lay.addWidget(label("Заметки — по желанию: книга, курс, что уже знаете", "Muted"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(80)
        lay.addWidget(self.notes)
        lay.addWidget(label("Claude составит карту темы — 6–14 понятий в порядке изучения. Обычно 20–60 секунд.",
                            "Hint"))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Отмена", self.reject)
        self.ok = button("Составить карту", self._accept, primary=True)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self.title.returnPressed.connect(self._accept)

    def _accept(self) -> None:
        if self.title.text().strip():
            self.accept()

    def values(self) -> dict:
        idx = next((i for i, b in enumerate(self.level.buttons) if b.isChecked()), 0)
        return {"title": self.title.text().strip(), "goal": self.goal.text().strip(),
                "level": list(LEVELS)[idx], "notes": self.notes.toPlainText().strip()}


class LearnPage(Page):
    """Темы → страница темы (в той же странице: «‹ Все темы» возвращает к списку)."""

    def __init__(self, controller):
        super().__init__("Учёба", "Темы, карты понятий и сессии с Claude")
        self.c = controller
        self.topic_id: int | None = None
        self.new_btn = button("Новая тема…", self.c.new_topic_dialog, primary=True)
        self.head_right.addWidget(self.new_btn, 0, Qt.AlignTop)
        self.map: ConceptMap | None = None
        self.status_label: QLabel | None = None

    def refresh(self) -> None:
        if self.topic_id is not None and self.c.storage.topic(self.topic_id):
            self._topic_page(self.topic_id)
        else:
            self.topic_id = None
            self._list()

    def show_list(self) -> None:
        self.topic_id = None
        self.refresh()

    def open_topic(self, topic_id: int) -> None:
        self.topic_id = topic_id
        self.refresh()

    # ------------------------------------------------------------- список
    def _list(self) -> None:
        self.title.setText("Учёба")
        self.subtitle.setText("Темы, карты понятий и сессии с Claude")
        self.new_btn.show()
        self.clear_body()
        topics = self.c.storage.topics()
        if not topics:
            self.body.addWidget(label("Тем пока нет. Нажмите «Новая тема…» — Claude составит карту, и можно "
                                      "начинать.", "Hint"))
            return
        for t in topics:
            p = engine.topic_progress(self.c.storage, t["id"])
            card = W.Card("", "", padding=16)
            head = QHBoxLayout()
            title = label(t["title"], "TopicTitle")
            head.addWidget(title, 1)
            head.addWidget(button("Открыть", lambda tid=t["id"]: self.open_topic(tid)))
            card.add_layout(head)
            if t["goal"]:
                card.add(label(t["goal"], "Muted"))
            card.add(topic_bar(p))
            info = label(progress_text(p), "Meta")
            info.setToolTip(MASTERY_TIP)
            card.add(info)
            self.body.addWidget(card)

    # ------------------------------------------------------------- тема
    def _topic_page(self, topic_id: int) -> None:
        st = self.c.storage
        t = st.topic(topic_id)
        self.title.setText(t["title"])
        self.subtitle.setText(t["goal"] or LEVELS.get(t["level"], ""))
        self.new_btn.hide()
        self.clear_body()
        top = QHBoxLayout()
        top.addWidget(button("‹  Все темы", self.show_list, name="Link"))
        top.addStretch(1)
        more = QPushButton("⋯")
        more.setObjectName("Icon")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self._menu(more, topic_id))
        top.addWidget(more)
        self.body.addLayout(top)
        concepts = st.concepts(topic_id)
        plan = self.c.plan_for(topic_id)
        card = W.Card("", "", padding=18)
        if plan.open_loop:
            card.add(label("В прошлый раз остановились на:", "Muted"))
            card.add(label(f"«{plan.open_loop}»", "Loop"))
        card.add(label(f"Сегодня: {plan.summary()}", "Big"))
        if plan.note:
            card.add(label(plan.note, "Muted"))
        row = QHBoxLayout()
        self.start_btn = button("Начать сессию", lambda: self.c.start_learning(topic_id), primary=True)
        self.start_btn.setEnabled(bool(concepts))
        row.addWidget(self.start_btn)
        due = engine.topic_progress(st, topic_id)["due"]
        if due:
            row.addWidget(button(f"Повторить ({due})", lambda: self.c.window.open_review(topic_id)))
        row.addStretch(1)
        card.add_layout(row)
        self.body.addWidget(card)
        g = W.Group("Карта темы", "Наверху — с чего тема начинается, ниже — то, что на этом держится. "
                                  "Синие — пройдены и закрепляются повторением, зелёные — закреплены "
                                  "(помните через три недели).")
        if concepts:
            self.map = ConceptMap()
            self.map.set_concepts(concepts)
            self.map.concept_clicked.connect(self._concept_info)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 12, 0, 12)
            wl.addWidget(self.map)
            g.add_widget(wrap)
        else:
            self.status_label = label("Карта ещё не составлена.", "Muted")
            g.add_widget(self.status_label)
            g.add_widget(button("Составить карту с Claude", lambda: self.c.build_map(topic_id), primary=True))
        self.body.addWidget(g)
        cps = st.checkpoints(topic_id, 5)
        if cps:
            g = W.Group("Где останавливались")
            for cp in cps:
                text = cp["covered"] or "—"
                extra = " · ".join(x for x in (f"трудно: {cp['difficulties']}" if cp["difficulties"] else "",
                                               f"теперь можете: {cp['now_can']}" if cp["now_can"] else "") if x)
                g.add_row(f"{when_text(cp['ts'])}: {text}", extra)
            self.body.addWidget(g)

    def set_map_status(self, text: str) -> None:
        if self.status_label is not None:
            try:
                self.status_label.setText(text)
            except RuntimeError:
                pass

    def _concept_info(self, concept_id: int) -> None:
        c = self.c.storage.concept(concept_id)
        if c:
            status = {"new": "впереди", "learning": "пройдено, закрепляется повторением",
                      "mastered": "закреплено"}.get(c["status"], "")
            self.c.toast(f"{c['title']} — {status}. {c['summary']}")

    def _menu(self, anchor, topic_id: int) -> None:
        menu = QMenu(self.area)
        menu.addAction("Составить карту заново", lambda: self.c.build_map(topic_id, replace=True))
        menu.addAction("Начать разговор с Claude заново",
                       lambda: (self.c.storage.update_topic(topic_id, claude_session="", claude_tokens=0),
                                self.c.toast("Следующая сессия начнёт новый разговор с текущим состоянием темы.")))
        menu.addSeparator()
        menu.addAction("В архив", lambda: self.c.archive_topic(topic_id))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class StepChips(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.chips: dict[str, QLabel] = {}
        for key, text in STEP_LABELS.items():
            chip = QLabel(text.upper())
            chip.setObjectName("StepChip")
            self.chips[key] = chip
            lay.addWidget(chip)

    def set_step(self, step: str) -> None:
        keys = list(STEP_LABELS)
        idx = keys.index(step) if step in keys else -1
        for k, key in enumerate(keys):
            chip = self.chips[key]
            chip.setProperty("state", "now" if k == idx else "done" if k < idx else "")
            chip.style().unpolish(chip)
            chip.style().polish(chip)


class LearnSessionView(QWidget):
    """Сессия во всё окно: сверху тема, шаги и время; в центре разговор; снизу ответ."""

    back = Signal()

    def __init__(self, controller):
        super().__init__()
        self.c = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar_ = QFrame()
        bar_.setObjectName("TopBar")
        tl = QHBoxLayout(bar_)
        tl.setContentsMargins(20, 10, 20, 10)
        self.back_btn = button("‹  Тема", lambda: self.c.learn_leave(), name="Link")
        tl.addWidget(self.back_btn)
        tl.addSpacing(10)
        self.topic_label = label("", "Meta", wrap=False)
        tl.addWidget(self.topic_label)
        tl.addStretch(1)
        self.steps = StepChips()
        tl.addWidget(self.steps)
        tl.addStretch(1)
        self.timer_label = label("", "Meta", wrap=False)
        tl.addWidget(self.timer_label)
        tl.addSpacing(10)
        self.end_btn = button("Закончить", lambda: self.c.learn_end())
        tl.addWidget(self.end_btn)
        outer.addWidget(bar_)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setObjectName("SessionSplit")
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        self.chat = ChatView()
        self.chat.setMinimumWidth(340)
        self.split.addWidget(self.chat)
        self.board = BoardPanel()
        self.board.setMinimumWidth(460)
        self.board.hide()
        self.split.addWidget(self.board)
        outer.addWidget(self.split, 1)
        bottom = QFrame()
        bottom.setObjectName("BottomBar")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(36, 12, 36, 16)
        self.input = ChatInput("Ваш ответ или вопрос… (Enter — отправить, Shift+Enter — новая строка)",
                               confidence=True)
        self.input.setMaximumWidth(760)
        self.input.submitted.connect(lambda text, conf: self.c.learn_send(text, conf))
        bl.addStretch(1)
        bl.addWidget(self.input, 100)
        bl.addStretch(1)
        outer.addWidget(bottom)
        self.board_btn = QPushButton("Доска")
        self.board_btn.setObjectName("BoardChip")
        self.board_btn.setCheckable(True)
        self.board_btn.setCursor(Qt.PointingHandCursor)
        self.board_btn.setToolTip("Доска для схем от руки — Ctrl+D. Claude получит схему текстом (Mermaid)")
        self.board_btn.clicked.connect(self.toggle_board)
        self.input.conf_layout.addWidget(self.board_btn)
        sc = QShortcut(QKeySequence("Ctrl+D"), self)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self.toggle_board)
        self.board.closed.connect(self.close_board)
        self.board.wide_toggled.connect(self.set_wide)
        self.board.send.connect(lambda data, mermaid, png: self.c.learn_send_board(data, mermaid, png))
        self.board.changed.connect(lambda: self._draft.start())
        self.chat.schema_open.connect(self.open_schema)
        self.chat.board_open.connect(lambda: self.open_board())
        self._draft = QTimer(self)
        self._draft.setSingleShot(True)
        self._draft.setInterval(1500)
        self._draft.timeout.connect(lambda: self.c.save_board_draft())

    # ------------------------------------------------------------- доска
    def toggle_board(self) -> None:
        if self.board.isVisible():
            self.close_board()
        else:
            self.open_board()

    def open_board(self, board=None) -> None:
        if board is not None:
            if self.board.board.is_empty():
                self.board.load(board)
            else:
                self.board.load(board, keep_history=True)
                self.c.toast("Схема Claude на доске. Ctrl+Z вернёт ваш рисунок.")
        texts = self.chat.assistant_texts()
        self.board.set_task(task_from(texts[-1]) if texts else "")
        if not self.board.isVisible():
            self.board.show()
            total = max(800, self.split.width())
            self.split.setSizes([max(340, int(total * 0.38)), int(total * 0.62)])
        self.board_btn.setChecked(True)
        self.board.canvas.setFocus()

    def open_schema(self, board) -> None:
        self.open_board(board)

    def close_board(self) -> None:
        if self.board.isVisible():
            self.board.canvas.commit_edit()
            self.c.save_board_draft()
        self.set_wide(False)
        self.board.hide()
        self.board_btn.setChecked(False)
        self.input.edit.setFocus()

    def set_wide(self, wide: bool) -> None:
        self.chat.setVisible(not wide)
        self.board.wide_btn.setChecked(wide)

    def suggest_board(self, on: bool) -> None:
        """Claude просит нарисовать схему — кнопка доски заметнее (но доска не открывается сама)."""
        self.board_btn.setProperty("suggest", bool(on))
        self.board_btn.setText("Нарисовать на доске" if on else "Доска")
        self.board_btn.style().unpolish(self.board_btn)
        self.board_btn.style().polish(self.board_btn)
        self.board_btn.setMinimumWidth(self.board_btn.sizeHint().width())   # жирный шрифт шире обычного
        self.board_btn.updateGeometry()

    def after_answer(self, text: str, step: str, recall_arm: str = "") -> None:
        self.suggest_board(wants_board(text, step, recall_arm))
        if self.board.isVisible():
            self.board.set_task(task_from(text))

    def set_busy(self, busy: bool) -> None:
        self.input.set_busy(busy)
        self.board.set_busy(busy)

    def begin(self, topic: dict, formats: dict) -> None:
        self.chat.clear()
        self.board.load(None)
        self.close_board()
        self.suggest_board(False)
        self.back_btn.setText("‹  " + topic["title"][:28])
        plan = (self.c.storage.session(self.c.learn_sid) or {}).get("plan") or {}
        self.topic_label.setText(f"новое: {plan['concept_title']}" if plan.get("concept_title") else "")
        self.steps.set_step("")
        self.end_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.set_busy(False)
        fmt = ", ".join(bandit.arm_label(e, formats[e]).lower() for e in ("order", "present", "recall")
                        if e in formats)
        self.chat.add_note(f"Сегодня пробуем: {fmt}")

    def set_minutes(self, minutes: int) -> None:
        self.timer_label.setText(f"{minutes} мин")

    def finished_card(self, on_rate) -> QWidget:
        """«Как прошло?» — 1–5 звёзд. Оценка учит крючки и подарки и считает удовольствие."""
        card = W.Card("", "", padding=18)
        row = QHBoxLayout()
        row.addWidget(label("Как прошло?", "CardTitle"))
        row.addStretch(1)
        stars = []
        for i in range(1, 6):
            s = QPushButton("★")
            s.setObjectName("Star")
            s.setCheckable(True)
            s.setCursor(Qt.PointingHandCursor)
            s.setToolTip(f"{i} из 5")
            stars.append(s)
            row.addWidget(s)

        def choose(n: int):
            for k, s in enumerate(stars, start=1):
                s.setChecked(k <= n)
                s.setEnabled(False)
            on_rate(n)
        for k, s in enumerate(stars, start=1):
            s.clicked.connect(lambda _=False, n=k: choose(n))
        card.add_layout(row)
        card.add(label("Оценка нужна не для отчёта: по ней система понимает, какие крючки и подарки вам заходят.",
                       "Muted"))
        self.stars = stars
        return card

    def summary_card(self, s: dict, on_done) -> QWidget:
        card = W.Card("", "", padding=18)
        card.add(label("ИТОГ СЕССИИ", "SectionLabel"))
        if s.get("now_can"):
            card.add(label(f"Теперь вы можете: {s['now_can']}", "Big"))
        facts = [f"{s['minutes']} мин", f"ответов {s['answers']}, верно {s['correct']}"]
        if s.get("cards"):
            facts.append(f"карточек для повторения: {s['cards']}")
        card.add(label(" · ".join(facts), "Muted"))
        if s.get("open_loop"):
            card.add(label("В следующий раз начнём с вопроса:", "Muted"))
            card.add(label(f"«{s['open_loop']}»", "Loop"))
        card.add(button("Вернуться к теме", on_done, primary=True))
        return card

    def error_card(self, text: str, fix_label: str | None, fix, retry) -> QWidget:
        return error_card(text, fix_label, fix, retry)


_DRAW = re.compile(r"нарису|начерти|изобрази|на доске|(?:сделай|составь|собери|построй|набросай)\s+(?:\w+\s+)?схем"
                   r"|схем\w*\s+по\s+памяти", re.I)
_FENCED = re.compile(r"```.*?(?:```|$)", re.S)


def wants_board(text: str, step: str = "", recall_arm: str = "") -> bool:
    """Просит ли Claude нарисовать схему: по концу сообщения (там вопрос) или по формату закрепления.
    Схема, которую Claude показал сам (блок кода), — не просьба."""
    prose = _FENCED.sub("\n\n", text or "")
    paras = [p for p in prose.strip().split("\n\n") if p.strip()]
    if _DRAW.search(" ".join(paras[-2:])):
        return True
    return step == "recall" and recall_arm == "schema_recall"


def task_from(text: str) -> str:
    """Задание для шапки доски: последний абзац с вопросом или просьбой нарисовать."""
    paras = [p.strip() for p in (text or "").strip().split("\n\n") if p.strip() and not p.strip().startswith("```")]
    if not paras:
        return ""
    pick = next((p for p in reversed(paras) if "?" in p or _DRAW.search(p)), paras[-1])
    pick = re.sub(r"[*_`#>]+", "", pick).replace("\n", " ").strip()
    return pick if len(pick) <= 260 else pick[:257].rsplit(" ", 1)[0] + "…"


def error_card(text: str, fix_label: str | None, fix, retry) -> QWidget:
    """Сбой в чате: понятная причина, кнопка, которая её исправляет, и «Повторить»."""
    card = QFrame()
    card.setObjectName("ErrorCard")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(14, 10, 10, 10)
    lay.addWidget(label(text), 1)
    if fix_label and fix:
        lay.addWidget(button(fix_label, fix, primary=True))
    if retry:
        lay.addWidget(button("Повторить", retry))
    return card


def clear(layout) -> None:
    clear_layout(layout)
