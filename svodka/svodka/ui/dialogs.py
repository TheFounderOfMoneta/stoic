"""Небольшие окна: сообщение с действием и «Почему эта статья здесь»."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

from ..rank.features import ftype, human
from . import widgets as W

PARTS = [("personal", "Ваш вкус", "прогноз по тому, что вы читаете, дочитываете и отмечаете"),
         ("fit", "Профиль", "насколько совпадает с вашим описанием интересов (оценка Claude)"),
         ("importance", "Важность", "насколько событие значимо в мире"),
         ("fresh", "Свежесть", "чем новее, тем выше")]


class Dialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, width: int = 520):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(26, 24, 26, 20)
        self.lay.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("DialogTitle")
        t.setWordWrap(True)
        self.lay.addWidget(t)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch(1)

    def text(self, text: str, name: str = "Soft") -> QLabel:
        lab = QLabel(text)
        lab.setObjectName(name)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lay.addWidget(lab)
        return lab

    def button(self, label: str, fn=None, primary: bool = False, close: bool = True) -> QPushButton:
        b = QPushButton(label)
        b.setCursor(Qt.PointingHandCursor)
        if primary:
            b.setObjectName("Primary")
            b.setDefault(True)

        def clicked():
            if close:
                self.accept()
            if fn is not None:
                fn()
        b.clicked.connect(clicked)
        self.buttons.addWidget(b)
        return b

    def finish(self) -> None:
        self.lay.addSpacing(6)
        self.lay.addLayout(self.buttons)


def message_dialog(parent, title: str, text: str, code: str = "", buttons: list | None = None) -> Dialog:
    d = Dialog(parent, title)
    d.text(text)
    if code:
        box = QPlainTextEdit(code)
        box.setObjectName("CodeBox")
        box.setReadOnly(True)
        box.setFixedHeight(28 + 20 * code.count("\n"))
        d.lay.addWidget(box)
        copy = QPushButton("Скопировать команду")
        copy.setObjectName("Link")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(code), copy.setText("Скопировано")))
        d.lay.addWidget(copy, 0, Qt.AlignLeft)
    for label, fn, primary in buttons or []:
        d.button(label, fn, primary)
    d.button("Закрыть")
    d.finish()
    d.open()
    return d


def _pct(v: float) -> str:
    return f"{round(float(v) * 100)}%"


def explain_dialog(parent, item, model) -> Dialog:
    """Прозрачность: из чего сложилась оценка статьи и что лента о вас знает."""
    d = Dialog(parent, "Почему эта статья здесь", width=560)
    a = item.article
    d.text(a.get("title_ru") or a.get("title_orig") or "", "Muted")
    if item.reasons:
        d.text("\n".join("•  " + r[0].upper() + r[1:] for r in item.reasons))
    card = W.Card("Из чего сложилась оценка", "Чем больше вы читаете, тем больше решает ваш вкус", padding=16)
    for key, name, hint in PARTS:
        row = QHBoxLayout()
        row.addWidget(QLabel(name), 1)
        val = QLabel(f"{_pct(item.parts.get(key, 0))}  ·  вес {_pct(item.weights.get(key, 0))}")
        val.setObjectName("Soft")
        row.addWidget(val, 0, Qt.AlignRight)
        card.add_layout(row)
        h = QLabel(hint)
        h.setObjectName("Muted")
        h.setWordWrap(True)
        card.add(h)
    d.lay.addWidget(card)
    known = []
    # средние оценки модели, а не случайная выборка Томпсона, которой ранжируется лента
    stats = [(f, model.stats.get(f)) for f, _b, _w in item.personal.detail]
    for f, st in sorted(((f, st) for f, st in stats if st is not None), key=lambda x: abs(x[1].beta),
                        reverse=True):
        if ftype(f) == "len" or st.ev_long < 1.0:
            continue
        mood = "нравится" if st.beta > 0.1 else ("скорее не нравится" if st.beta < -0.1 else "нейтрально")
        known.append(f"{human(f, model.display)} — {mood}")
        if len(known) >= 5:
            break
    if known:
        d.text("Что лента знает о вас по этой статье:\n" + "\n".join("•  " + k for k in known))
    if item.explore:
        d.text(("Это разведка: тема для вас новая." if getattr(item, "novel_topic", True) else
                "Ваша тема, но о таких статьях лента пока мало знает.")
               + " Отметьте «Интересно» или «Не моё» — лента быстро поймёт.", "Muted")
    d.button("Понятно", primary=True)
    d.finish()
    d.open()
    return d
