"""«Разговор»: выговориться отдельно от учёбы.

Здесь нет ни одной механики удержания: ни серий, ни наград, ни открытых петель, ни напоминаний.
Режим можно сменить посреди разговора. В начале и в конце — самочувствие 0–10: по разнице
видно, какой режим и какой приём вам реально помогают. Сводку для памяти вы видите и решаете сами.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSlider, QVBoxLayout, QWidget

from ..talk import FEELINGS, MODE_HINTS, MODES
from . import widgets as W
from .chat import ChatInput, ChatView
from .pages import Page, button, clear_layout, label


class MoodPicker(QWidget):
    """Самочувствие 0–10: большой номер и ползунок."""

    def __init__(self, value: int = 5):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("MoodValue")
        self.value_label.setFixedWidth(40)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 10)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(str(v)))
        lay.addWidget(self.value_label)
        lay.addWidget(label("плохо", "Muted", wrap=False))
        lay.addWidget(self.slider, 1)
        lay.addWidget(label("отлично", "Muted", wrap=False))

    def value(self) -> int:
        return int(self.slider.value())


class TalkPage(Page):
    def __init__(self, controller):
        super().__init__("Разговор", "Выговориться — без оценок, серий и напоминаний")
        self.c = controller

    def refresh(self) -> None:
        self.clear_body()
        s = self.c.settings
        card = W.Card("", "", padding=20)
        card.add(label("КАК ПОГОВОРИМ", "SectionLabel"))
        self.mode = W.Segmented(list(MODES.values()))
        default = list(MODES).index(s.get("talk.default_mode", "listen"))
        self.mode.buttons[default].setChecked(True)
        self.mode_hint = label(MODE_HINTS[list(MODES)[default]], "Muted")
        self.mode.changed.connect(lambda i: self.mode_hint.setText(MODE_HINTS[list(MODES)[i]]))
        card.add(self.mode)
        card.add(self.mode_hint)
        card.add(label("КАК ТЫ СЕЙЧАС", "SectionLabel"))
        self.mood = MoodPicker(5)
        card.add(self.mood)
        wrap = QWidget()
        rows = QVBoxLayout(wrap)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self.feeling_buttons = []
        for start in range(0, len(FEELINGS), 5):          # по пять в строке — слова не обрезаются
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for f in FEELINGS[start:start + 5]:
                b = QPushButton(f)
                b.setObjectName("Chip")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                self.feeling_buttons.append(b)
                chips.addWidget(b)
            chips.addStretch(1)
            rows.addLayout(chips)
        card.add(label("Если хочется — слово для чувства (можно несколько или ни одного)", "Muted"))
        card.add(wrap)
        row = QHBoxLayout()
        self.start_btn = button("Начать разговор", self._start, primary=True)
        row.addWidget(self.start_btn)
        row.addStretch(1)
        card.add_layout(row)
        privacy = "Переписка удалится после разговора — и здесь, и в журнале Claude Code." \
            if not s.get("talk.keep_transcripts") else "Переписка сохраняется (Настройки → Разговор)."
        card.add(label(privacy, "Hint"))
        self.body.addWidget(card)
        stats = self.c.talk_stats()
        if stats["modes"]:
            g = W.Group("Что помогает", "Изменение самочувствия от начала к концу разговора.")
            for m, v in sorted(stats["modes"].items(), key=lambda kv: -kv[1]["delta"]):
                sign = "+" if v["delta"] > 0 else ""
                g.add_row(MODES.get(m, m), f"разговоров: {v['n']}", label(f"{sign}{v['delta']:.1f}", "Soft",
                                                                          wrap=False))
            self.body.addWidget(g)
        notes = self.c.storage.talk_notes(30)
        if notes:
            if stats["themes"]:
                self.body.addWidget(label("Частые темы: " + ", ".join(f"{t} ({n})" for t, n in stats["themes"]),
                                          "Muted"))
            g = W.Group("Память", "Только то, что вы разрешили запомнить. Удалить можно любую запись.")
            for n in notes[:12]:
                extra = ", ".join(n["themes"])
                if n["helped"]:
                    extra += f" · помогло: {n['helped']}"
                g.add_row(n["summary"], extra, button("Забыть", lambda nid=n["id"]: self._forget(nid), name="Link"))
            self.body.addWidget(g)

    def _forget(self, note_id: int) -> None:
        self.c.storage.delete_talk_note(note_id)
        self.refresh()

    def _start(self) -> None:
        idx = next((i for i, b in enumerate(self.mode.buttons) if b.isChecked()), 0)
        feeling = ", ".join(b.text() for b in self.feeling_buttons if b.isChecked())
        self.c.talk_start(list(MODES)[idx], self.mood.value(), feeling)


class TalkSessionView(QWidget):
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
        self.back_btn = button("‹  Разговор", lambda: self.c.talk_end(), name="Link")
        tl.addWidget(self.back_btn)
        tl.addStretch(1)
        self.mode = W.Segmented(list(MODES.values()))
        self.mode.changed.connect(lambda i: self.c.talk_set_mode(list(MODES)[i]))
        tl.addWidget(self.mode)
        tl.addStretch(1)
        self.end_btn = button("Завершить", lambda: self.c.talk_end())
        tl.addWidget(self.end_btn)
        outer.addWidget(bar_)
        self.chat = ChatView()
        outer.addWidget(self.chat, 1)
        bottom = QFrame()
        bottom.setObjectName("BottomBar")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(36, 12, 36, 16)
        self.input = ChatInput("Пиши как есть… (Enter — отправить, Shift+Enter — новая строка)")
        self.input.setMaximumWidth(760)
        self.input.submitted.connect(lambda text, _conf: self.c.talk_send(text))
        bl.addStretch(1)
        bl.addWidget(self.input, 100)
        bl.addStretch(1)
        outer.addWidget(bottom)

    def begin(self, mode: str) -> None:
        self.chat.clear()
        self.mode.blockSignals(True)
        self.mode.buttons[list(MODES).index(mode)].setChecked(True)
        self.mode.blockSignals(False)
        self.end_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.set_busy(False)
        self.chat.add_note(f"{MODES[mode]}. {MODE_HINTS[mode]}. Режим можно сменить сверху в любой момент.")

    def mood_card(self, on_done) -> QWidget:
        card = W.Card("", "", padding=18)
        card.add(label("КАК ТЫ СЕЙЧАС", "SectionLabel"))
        picker = MoodPicker(5)
        card.add(picker)
        done = button("Готово", lambda: (done.setEnabled(False), on_done(picker.value())), primary=True)
        card.add(done)
        self.mood_picker = picker
        self.mood_done = done
        return card

    def memory_card(self, note: dict, on_keep, on_drop) -> QWidget:
        """Сводка для памяти: видно целиком, можно поправить, решение — ваше."""
        card = W.Card("", "", padding=18)
        card.add(label("ЗАПОМНИТЬ ЭТО?", "SectionLabel"))
        edit = QPlainTextEdit(note["summary"])
        edit.setFixedHeight(76)
        card.add(edit)
        if note.get("themes"):
            card.add(label("Темы: " + ", ".join(note["themes"]), "Muted"))
        row = QHBoxLayout()
        keep = button("Запомнить", lambda: on_keep({**note, "summary": edit.toPlainText().strip() or note["summary"]}),
                      primary=True)
        drop = button("Не запоминать", on_drop)
        row.addWidget(keep)
        row.addWidget(drop)
        row.addStretch(1)
        card.add_layout(row)
        card.add(label("Запомненное видно в «Разговоре» → «Память» и помогает не начинать каждый раз с нуля.",
                       "Hint"))
        self.keep_btn, self.drop_btn, self.memory_edit = keep, drop, edit
        return card

    def done_card(self, text: str, on_done) -> QWidget:
        card = W.Card("", "", padding=18)
        card.add(label(text, "Big"))
        card.add(button("Вернуться", on_done, primary=True))
        return card


def clear(layout) -> None:
    clear_layout(layout)
