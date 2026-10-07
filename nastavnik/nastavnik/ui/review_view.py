"""Повторение: карточки по сроку. Ответ проверяет Claude — сам ставит оценку и коротко объясняет.

Как идёт карточка: вопрос → пишете ответ своими словами (или рисуете схему на доске) → Enter →
сразу виден эталон, а через несколько секунд — вердикт Claude: оценка 1–4 и пояснение в одну-две
фразы. Оценка сразу уходит в FSRS: от неё зависит, когда карточка вернётся. Ждать не обязательно:
«Дальше» можно нажать сразу — проверка закончится в фоне, а вердикт появится в «Проверено» ниже.
«Не помню» (или пустой ответ) — «не вспомнил» без вопросов к Claude.

Время от вопроса до ответа — время вспоминания: из него план узнаёт, сколько у вас занимает
карточка, а Claude отличает «верно» от «верно и легко». Первое повторение понятия через несколько
дней — отложенный тест: по нему проверяются форматы.

Если Claude недоступен (вход, лимит, сеть) или проверка выключена в Настройках, оцениваете сами,
как раньше: 1–4 с подсказкой, когда карточка вернётся.

Повторить заранее можно когда угодно: карточки, у которых срок ещё не подошёл, — сначала ближайшие.
"""
from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .. import claude_cli, sketch
from ..learn import engine, fsrs
from ..review_check import NO_ANSWER, VERDICTS
from ..util import ahead_text, plural
from . import widgets as W
from .board import BoardPanel, SchemaView
from .pages import Page, button, clear_layout, label

UNAVAILABLE = ("auth", "not_installed", "limit", "network")
MARKS = {1: "✗", 2: "◐", 3: "✓", 4: "✓"}


def answer_schema(answer: str) -> sketch.Board | None:
    """Ответ карточки — схема Mermaid (с ``` или без)? Тогда её можно нарисовать картинкой."""
    text = (answer or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("mermaid"):
            text = text[7:].strip()
    from .board import schema_from_mermaid
    return schema_from_mermaid(text)


class AnswerEdit(QPlainTextEdit):
    """Поле ответа: Enter — проверить, Shift+Enter — новая строка."""

    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("ChatInput")
        self.setTabChangesFocus(True)
        self.setPlaceholderText("Напишите ответ своими словами — проверит Claude. Enter — проверить, "
                                "Shift+Enter — новая строка")
        self.setFixedHeight(64)
        self.textChanged.connect(self._grow)

    def _grow(self) -> None:
        lines = int(self.document().size().height())
        self.setFixedHeight(max(64, min(150, lines * self.fontMetrics().lineSpacing() + 24)))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class ReviewPage(Page):
    def __init__(self, controller):
        super().__init__("Повторение", "Карточки, у которых подошёл срок")
        self.c = controller
        self.queue: list[dict] = []
        self.topic_id: int | None = None
        self.current: dict | None = None
        self.state = ""                  # answer — отвечаете; checked — ответ у Claude; manual — оцениваете сами
        self.shown_at = 0.0
        self.revealed_at = 0.0
        self.done = 0
        self.run = 0                     # номер прохода очереди: ответы Claude из прошлого прохода не трогают текущий
        self.pending = 0
        self.manual_reason = ""
        self.session_id: int | None = None
        self.ahead = False
        self.drawn: sketch.Board | None = None
        self._shown: tuple | None = None
        # --- карточка
        self.card = W.Card("", "", padding=26)
        self.meta = label("", "SectionLabel")
        self.question = label("", "Question")
        self.question.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.attempt = AnswerEdit()
        self.attempt.submitted.connect(self.submit)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        self.check_btn = button("Проверить  ·  Enter", self.submit, primary=True)
        self.dunno_btn = button("Не помню", self.dunno)
        self.dunno_btn.setToolTip("Честное «не вспомнил»: карточка вернётся скоро, Claude не нужен")
        self.draw_btn = button("Нарисовать на доске", self.open_board)
        self.draw_btn.setToolTip("Нарисуйте схему от руки — Claude сравнит её с эталоном")
        for b in (self.check_btn, self.dunno_btn, self.draw_btn):
            al.addWidget(b)
        al.addStretch(1)
        self.board = BoardPanel(send_label="Готово — проверить", mermaid_title="Схема текстом")
        self.board.setMinimumHeight(470)
        self.board.close_btn.setToolTip("Свернуть доску")
        self.board.wide_btn.hide()
        self.board.closed.connect(self.close_board)
        self.board.send.connect(self._board_done)
        self.board.hide()
        self.mine = QWidget()
        self.mine_lay = QVBoxLayout(self.mine)
        self.mine_lay.setContentsMargins(0, 0, 0, 0)
        self.mine_lay.setSpacing(6)
        self.ref = QWidget()
        self.ref_lay = QVBoxLayout(self.ref)
        self.ref_lay.setContentsMargins(0, 0, 0, 0)
        self.ref_lay.setSpacing(6)
        self.answer = label("", "Answer")
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.verdict = QFrame()
        self.verdict.setObjectName("Verdict")
        vl = QVBoxLayout(self.verdict)
        vl.setContentsMargins(14, 10, 14, 11)
        vl.setSpacing(3)
        self.verdict_title = label("", "VerdictTitle")
        self.verdict_text = label("", "VerdictText")
        self.verdict_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        vl.addWidget(self.verdict_title)
        vl.addWidget(self.verdict_text)
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
        self.next_btn = button("Дальше  ·  Enter", self.next_card, primary=True)
        nr = QHBoxLayout()
        nr.addStretch(1)
        nr.addWidget(self.next_btn)
        self.next_row = QWidget()
        self.next_row.setLayout(nr)
        nr.setContentsMargins(0, 0, 0, 0)
        for w in (self.meta, self.question, self.attempt, self.actions, self.board, self.mine, self.ref,
                  self.verdict, self.grades, self.next_row):
            self.card.add(w)
        self.body.addWidget(self.card)
        # --- под карточкой
        self.banner = label("", "Muted")
        self.banner.hide()
        self.body.addWidget(self.banner)
        self.progress = label("", "Muted")
        self.body.addWidget(self.progress)
        self.empty = label("", "Hint")
        self.body.addWidget(self.empty)
        self.ahead_btn = button("Повторить заранее", lambda: self.start(self.topic_id, ahead=True), primary=True)
        self.ahead_btn.setToolTip("Понятия, которые повторили раньше чем через 1,5 дня после урока, не идут в проверку "
                                  "форматов — по ним приложение не будет судить, что вам подходит")
        self.ahead_note = label("Срок ещё не подошёл, но вспомнить можно и сейчас. Вспоминать раньше срока легче, "
                                "поэтому следующее повторение сдвинется меньше, чем после планового.", "Muted")
        row = QHBoxLayout()
        row.addWidget(self.ahead_btn)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(self.ahead_note)
        self.ahead_btn.hide()
        self.ahead_note.hide()
        self.feed = QWidget()
        self.feed_lay = QVBoxLayout(self.feed)
        self.feed_lay.setContentsMargins(0, 8, 0, 0)
        self.feed_lay.setSpacing(8)
        self.feed_lay.addWidget(label("ПРОВЕРЕНО", "SectionLabel"))
        self.feed.hide()
        self.body.addWidget(self.feed)
        # --- клавиши: какие работают, зависит от шага (см. _sync_keys)
        self.keys: dict[str, list[QShortcut]] = {"next": [], "grade": []}
        for key in ("Return", "Enter", "Space"):
            sc = QShortcut(QKeySequence(key), self.area)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(self.next_card)
            self.keys["next"].append(sc)
        for g in (1, 2, 3, 4):
            sc = QShortcut(QKeySequence(str(g)), self.area)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda g=g: self.grade(g))
            self.keys["grade"].append(sc)
        self._set_state("")

    # ------------------------------------------------------------- шаги
    @property
    def revealed(self) -> bool:
        return self.state in ("checked", "manual")

    def ai_check(self) -> bool:
        return bool(self.c.settings.get("review.ai_check", True)) and not self.manual_reason

    def _set_state(self, state: str) -> None:
        self.state = state
        answering = state == "answer"
        self.attempt.setVisible(answering and not self.board.isVisible())
        self.actions.setVisible(answering and not self.board.isVisible())
        if not answering:
            self.board.hide()
        self.mine.setVisible(self.revealed and self.mine_lay.count() > 0)
        self.ref.setVisible(self.revealed)
        self.verdict.setVisible(self.revealed and (state == "checked" or bool(self.verdict_title.text())))
        self.grades.setVisible(state == "manual")
        self.next_row.setVisible(state == "checked")
        if state == "checked":
            self.next_btn.setFocus()               # Enter и пробел — «Дальше», даже после «Не помню»
        self._sync_keys()

    def _sync_keys(self) -> None:
        drawing = self.board.isVisible()
        for sc in self.keys["next"]:
            sc.setEnabled(self.state == "checked" and not drawing)
        for sc in self.keys["grade"]:
            sc.setEnabled(self.state == "manual" and not drawing)

    # ------------------------------------------------------------- очередь
    def start(self, topic_id: int | None = None, ahead: bool = False) -> None:
        if self.session_id is not None and ahead != self.ahead:
            self.c.end_review_session()
        self.topic_id = topic_id
        self.ahead = ahead
        self.run += 1
        self.queue = self.c.review_ahead_queue(topic_id) if ahead else self.c.review_queue(topic_id)
        self.done = 0
        self.session_id = None
        self._update_banner()
        self._next()

    def _show_empty(self) -> None:
        self.current = None
        self._set_state("")
        self.card.hide()
        if self.pending:
            self.empty.setText("Claude дописывает проверку последних ответов…")
            self.empty.show()
            return
        self.c.end_review_session()
        if self.ahead:
            text = "Готово — всё повторили заранее." if self.done else "Карточек пока нет: их делает Claude в конце сессии."
        else:
            text = "На сегодня всё." if self.done else "Повторять сейчас нечего."
        nxt = self.c.next_due_text()
        self.empty.setText(text + (" " + nxt if nxt else ""))
        self.empty.show()
        upcoming = len(self.c.review_ahead_queue(self.topic_id))
        if upcoming and not (self.ahead and self.done):
            self.ahead_btn.setText(f"Повторить заранее · {upcoming} "
                                   f"{plural(upcoming, ('карточка', 'карточки', 'карточек'))}")
            self.ahead_btn.show()
            self.ahead_note.show()
        self.progress.setText(f"Повторено: {self.done}" if self.done else "")
        self.subtitle.setText("Карточки, у которых подошёл срок")

    def _next(self) -> None:
        self.ahead_btn.hide()
        self.ahead_note.hide()
        if not self.queue:
            self._show_empty()
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
        if self.ahead and item.get("due"):
            meta.append(f"заранее · по плану {ahead_text(item['due'])}")
        self.meta.setText("  ·  ".join(m for m in meta if m).upper())
        self.question.setText(item["prompt"])
        self.drawn = None
        self._shown = None
        self.board.hide()
        self.board.load(None)
        self.board.set_task("")
        clear_layout(self.mine_lay)
        clear_layout(self.ref_lay)
        self.verdict_title.setText("")
        self.verdict_text.setText("")
        self.draw_btn.setVisible(item["kind"] == "schema")
        self.check_btn.setText("Проверить  ·  Enter" if self.ai_check() else "Показать ответ  ·  Enter")
        self.attempt.clear()
        self._set_state("answer")
        self.attempt.setFocus()
        self.shown_at = time.time()
        self.revealed_at = 0.0
        self.progress.setText(f"Повторено: {self.done}" if self.done else "")
        self.subtitle.setText(("Заранее · осталось " if self.ahead else "Осталось ") + str(len(self.queue)))

    def next_card(self) -> None:
        """«Дальше»: проверка (если ещё идёт) закончится в фоне, вердикт появится в «Проверено»."""
        if self.state != "checked" or not self.queue:
            return
        if self._shown:                            # вердикт уходящей карточки — в «Проверено»
            self._feed(*self._shown)
        self.queue.pop(0)
        self.done += 1
        self._next()

    # ------------------------------------------------------------- ответ
    def submit(self) -> None:
        if self.state != "answer" or not self.current:
            return
        self._answered(self.attempt.toPlainText().strip())

    def dunno(self) -> None:
        if self.state == "answer" and self.current:
            self._answered("")

    def _answered(self, text: str, drawn: sketch.Board | None = None, png: bytes | None = None,
                  mermaid: str = "") -> None:
        item = self.current
        self.revealed_at = time.time()
        seconds = self.revealed_at - self.shown_at
        self._show_answers(text, drawn)
        if not self.ai_check():
            self._ask_self("")
            return
        if not text and drawn is None:                 # «не помню» — оценка ясна и без Claude
            self._set_state("checked")
            self._apply(self._token(), item, 1, NO_ANSWER, int(seconds * 1000), self.session_id, self.ahead)
            return
        self._pending_verdict()
        self._set_state("checked")
        token, sid, ahead, latency = self._token(), self.session_id, self.ahead, int(seconds * 1000)
        self.pending += 1
        self.c.review_check(item, mermaid or text, seconds, png,
                            lambda res: self._checked(token, item, res, latency, sid, ahead),
                            lambda exc: self._check_failed(token, item, exc))

    def _token(self) -> tuple:
        return (self.run, self.current["id"] if self.current else None, self.shown_at)

    def _is_current(self, token: tuple) -> bool:
        return token == self._token() and self.revealed

    def _show_answers(self, text: str, drawn: sketch.Board | None) -> None:
        """Ваш ответ и эталон — сразу, не дожидаясь Claude."""
        item = self.current
        clear_layout(self.mine_lay)
        clear_layout(self.ref_lay)
        if drawn is not None:
            self.mine_lay.addWidget(label("ТВОЯ СХЕМА · " + sketch.describe(drawn).upper(), "SectionLabel"))
            self.mine_lay.addWidget(SchemaView(drawn, max_h=260, clickable=False))
        elif text:
            self.mine_lay.addWidget(label("ТВОЙ ОТВЕТ", "SectionLabel"))
            mine = label(text, "Muted")
            mine.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.mine_lay.addWidget(mine)
        ref = answer_schema(item["answer"])
        self.ref_lay.addWidget(label("ЭТАЛОН", "SectionLabel"))
        if ref is not None:
            self.ref_lay.addWidget(SchemaView(ref, max_h=300, clickable=False))
        else:
            self.answer = label(item["answer"] or "—", "Answer")
            self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.ref_lay.addWidget(self.answer)

    def _mark(self, grade: str) -> None:
        for w in (self.verdict, self.verdict_title):
            w.setProperty("grade", grade)
            self._repolish(w)

    def _pending_verdict(self) -> None:
        self._mark("")
        self.verdict_title.setText("Claude проверяет ответ…")
        self.verdict_text.setText("Можно не ждать — «Дальше»: оценка встанет сама, вердикт появится ниже.")

    def _show_verdict(self, grade: int, explanation: str, when: str) -> None:
        self._mark(str(grade))
        self.verdict_title.setText(f"{MARKS[grade]}  {VERDICTS[grade]}  ·  {when}")
        self.verdict_text.setText(explanation)
        self.verdict.show()

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ------------------------------------------------------------- вердикт Claude
    def _checked(self, token: tuple, item: dict, res: dict, latency: int, sid, ahead: bool) -> None:
        self.pending = max(0, self.pending - 1)
        self._apply(token, item, int(res["grade"]), res.get("explanation", ""), latency, sid, ahead)

    def _apply(self, token: tuple, item: dict, grade: int, explanation: str, latency: int, sid, ahead: bool,
               own: bool = False) -> None:
        """Оценка — в FSRS; вердикт — в карточку (если она ещё на экране) и в «Проверено»."""
        note = ("своя оценка" if own else f"Claude: {explanation}")[:500]
        new = self.c.review_grade(item["id"], grade, latency, sid, ahead=ahead, note=note)
        same_run = token[0] == self.run
        if grade == fsrs.AGAIN:
            when = "вернётся в конце очереди" if same_run else "вернётся скоро"
        else:
            when = f"вернётся {ahead_text(new['due'])}" if new.get("due") else ""
        if self._is_current(token) and not own:
            self._show_verdict(grade, explanation, when)
            self._shown = (item, grade, explanation, when)
        elif not own:                                 # уже ушли с карточки — вердикт сразу в «Проверено»
            self._feed(item, grade, explanation, when)
        if grade == fsrs.AGAIN and same_run:          # «не вспомнил» — ещё раз в конце этой же очереди
            fresh = self.c.storage.item(item["id"])
            if fresh:
                self.queue.append(fresh)
        if self.current is None:
            self._next() if self.queue else self._show_empty()

    def _check_failed(self, token: tuple, item: dict, exc: Exception) -> None:
        self.pending = max(0, self.pending - 1)
        kind = getattr(exc, "kind", "failed")
        reason = exc.human() if isinstance(exc, claude_cli.ClaudeError) else f"Что-то пошло не так: {exc}"
        if kind in UNAVAILABLE:
            self.manual_reason = reason
            self._update_banner()
        if self._is_current(token):
            self._ask_self(f"Claude не проверил: {reason} Оцени сам:")
            return
        self._feed(item, 0, "Claude не проверил — карточка вернётся в конце очереди.", "")
        if token[0] == self.run:
            fresh = self.c.storage.item(item["id"])
            if fresh:
                self.queue.append(fresh)
        if self.current is None:
            self._next() if self.queue else self._show_empty()

    def _feed(self, item: dict, grade: int, explanation: str, when: str) -> None:
        mark = f"{MARKS[grade]} {VERDICTS[grade]}" if grade else "Не проверено"
        row = QLabel(f'<span style="opacity:0.75">{html.escape(item["prompt"][:90])}</span><br>'
                     f"<b>{mark}</b>{' · ' + html.escape(when) if when else ''}"
                     f"{' — ' + html.escape(explanation) if explanation else ''}")
        row.setObjectName("FeedRow")
        row.setWordWrap(True)
        row.setTextFormat(Qt.RichText)
        self.feed_lay.insertWidget(1, row)
        while self.feed_lay.count() > 9:
            old = self.feed_lay.takeAt(self.feed_lay.count() - 1).widget()
            if old is not None:
                old.deleteLater()
        self.feed.show()

    def _update_banner(self) -> None:
        if self.manual_reason:
            self.banner.setText(f"{self.manual_reason} Пока оценивайте сами — как раньше, 1–4.")
            self.banner.show()
        else:
            self.banner.hide()

    # ------------------------------------------------------------- своя оценка (без Claude)
    def _ask_self(self, title: str) -> None:
        self._mark("")
        self.verdict_title.setText(title)
        self.verdict_text.setText("")
        self.verdict.setVisible(bool(title))
        self._set_state("manual")
        previews = fsrs.preview(self.current, time.time(), engine.memory_factor(self.c.storage),
                                float(self.c.settings.get("learn.desired_retention", 0.9)))
        for g, text in previews.items():
            self.grade_hints[g].setText(text)
        self.grade_buttons[fsrs.GOOD].setFocus()

    def grade(self, g: int) -> None:
        if self.state != "manual" or not self.current:
            return
        latency = int(((self.revealed_at or time.time()) - self.shown_at) * 1000)
        item = self.queue.pop(0)
        self.current = None
        self.done += 1
        self._apply((self.run, None, 0.0), item, g, "", latency, self.session_id, self.ahead, own=True)

    # ------------------------------------------------------------- доска (карточки-схемы)
    def open_board(self) -> None:
        if not self.current or self.state != "answer":
            return
        self.board.show()
        self.attempt.hide()
        self.actions.hide()
        self._sync_keys()                          # пока рисуете, Enter, пробел и 1–4 — клавиши доски
        self.board.canvas.setFocus()

    def close_board(self) -> None:
        self.board.canvas.commit_edit()
        self.board.hide()
        if self.current and self.state == "answer":
            self.attempt.show()
            self.actions.show()
        self._sync_keys()

    def _board_done(self, data: dict, mermaid: str, png) -> None:
        if not self.current or self.state != "answer":
            return
        self.c.storage.save_board(data, mermaid, None, session_id=self.session_id,
                                  topic_id=self.current["topic_id"], item_id=self.current["id"], sent=True)
        drawn = sketch.Board.from_dict(data)
        self.drawn = drawn
        self.board.hide()
        prompt = sketch.board_prompt(drawn, image=png is not None)
        self._answered("", drawn=drawn, png=png, mermaid=prompt)
