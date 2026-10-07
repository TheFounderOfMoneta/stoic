"""Повторение: карточки по сроку, без Claude — быстро и не тратит лимиты.

Сначала вспомнить (можно записать ответ — так запоминается крепче), потом «Показать ответ»
(Пробел) и честная оценка 1–4: под каждой кнопкой видно, когда карточка вернётся. Время до
«Показать ответ» — это время вспоминания; из него план узнаёт, сколько у вас занимает карточка.
Первое повторение понятия через несколько дней — отложенный тест: по нему проверяются форматы.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..learn import engine, fsrs
from . import widgets as W
from .pages import Page, button, label


class ReviewPage(Page):
    def __init__(self, controller):
        super().__init__("Повторение", "Карточки, у которых подошёл срок")
        self.c = controller
        self.queue: list[dict] = []
        self.topic_id: int | None = None
        self.current: dict | None = None
        self.shown_at = 0.0
        self.revealed_at = 0.0
        self.done = 0
        self.session_id: int | None = None
        self.card = W.Card("", "", padding=26)
        self.meta = label("", "SectionLabel")
        self.question = label("", "Question")
        self.question.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.attempt = QLineEdit()
        self.attempt.setPlaceholderText("Сначала вспомните. Можно записать ответ — так запоминается крепче")
        self.attempt.returnPressed.connect(self.reveal)
        self.answer = label("", "Answer")
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reveal_btn = button("Показать ответ  ·  Пробел", self.reveal, primary=True)
        self.grades = QWidget()
        gl = QHBoxLayout(self.grades)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(8)
        self.grade_buttons: dict[int, QPushButton] = {}
        self.grade_hints: dict[int, QLabel] = {}
        for g in (fsrs.AGAIN, fsrs.HARD, fsrs.GOOD, fsrs.EASY):
            col = QVBoxLayout()
            col.setSpacing(3)
            b = button(f"{g} · {fsrs.GRADE_LABELS[g]}", lambda g=g: self.grade(g), primary=(g == fsrs.GOOD))
            hint = label("", "GradeHint")
            hint.setAlignment(Qt.AlignCenter)
            col.addWidget(b)
            col.addWidget(hint)
            gl.addLayout(col, 1)
            self.grade_buttons[g] = b
            self.grade_hints[g] = hint
        for w in (self.meta, self.question, self.attempt, self.answer, self.reveal_btn, self.grades):
            self.card.add(w)
        self.body.addWidget(self.card)
        self.progress = label("", "Muted")
        self.body.addWidget(self.progress)
        self.empty = label("", "Hint")
        self.body.addWidget(self.empty)
        for key, fn in (("Space", self._space), ("1", lambda: self._key(1)), ("2", lambda: self._key(2)),
                        ("3", lambda: self._key(3)), ("4", lambda: self._key(4))):
            sc = QShortcut(QKeySequence(key), self.area)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ------------------------------------------------------------- очередь
    def start(self, topic_id: int | None = None) -> None:
        self.topic_id = topic_id
        self.queue = self.c.review_queue(topic_id)
        self.done = 0
        self.session_id = None
        self._next()

    def _next(self) -> None:
        if not self.queue:
            self.current = None
            self.card.hide()
            self.c.end_review_session()
            text = "На сегодня всё." if self.done else "Повторять сейчас нечего."
            nxt = self.c.next_due_text()
            self.empty.setText(text + (" " + nxt if nxt else ""))
            self.empty.show()
            self.progress.setText(f"Повторено: {self.done}" if self.done else "")
            self.subtitle.setText("Карточки, у которых подошёл срок")
            return
        if self.session_id is None:
            self.session_id = self.c.begin_review_session(self.topic_id)
        self.current = self.queue[0]
        item = self.current
        self.empty.hide()
        self.card.show()
        topic = self.c.storage.topic(item["topic_id"]) or {}
        concept = self.c.storage.concept(item["concept_id"]) if item["concept_id"] else None
        is_test = engine.is_delayed_test(self.c.storage, item, time.time())
        meta = [topic.get("title", "")]
        if concept:
            meta.append(concept["title"])
        if is_test:
            meta.append("проверка: помните ли через несколько дней")
        if item["kind"] == "schema":
            meta.append("схема по памяти")
        self.meta.setText("  ·  ".join(m for m in meta if m).upper())
        self.question.setText(item["prompt"])
        self.answer.setText(item["answer"] or "—")
        self.answer.hide()
        self.grades.hide()
        self.attempt.clear()
        self.attempt.show()
        self.reveal_btn.show()
        self.attempt.setFocus()
        self.shown_at = time.time()
        self.revealed_at = 0.0
        left = len(self.queue)
        self.progress.setText(f"Повторено: {self.done}" if self.done else "")
        self.subtitle.setText(f"Осталось {left}")

    def reveal(self) -> None:
        if not self.current or self.answer.isVisible():
            return
        self.revealed_at = time.time()
        self.answer.show()
        self.grades.show()
        self.reveal_btn.hide()
        previews = fsrs.preview(self.current, time.time(), engine.memory_factor(self.c.storage),
                                float(self.c.settings.get("learn.desired_retention", 0.9)))
        for g, text in previews.items():
            self.grade_hints[g].setText(text)
        self.grade_buttons[fsrs.GOOD].setFocus()

    def grade(self, g: int) -> None:
        if not self.current or not self.answer.isVisible():
            return
        latency = int(((self.revealed_at or time.time()) - self.shown_at) * 1000)
        item = self.queue.pop(0)
        self.c.review_grade(item["id"], g, latency, self.session_id)
        self.done += 1
        if g == fsrs.AGAIN:                      # «Снова» — вернётся в конце этой же очереди
            fresh = self.c.storage.item(item["id"])
            if fresh:
                self.queue.append(fresh)
        self._next()

    def _space(self) -> None:
        if self.attempt.hasFocus() and self.attempt.text():
            return
        self.reveal()

    def _key(self, g: int) -> None:
        if self.attempt.hasFocus() and not self.answer.isVisible():
            return
        self.grade(g)
