"""Небольшие окна (из «Сводки»): сообщение с действием и командой, вопрос с вариантами."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


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


def message_dialog(parent, title: str, text: str, code: str = "", buttons: list | None = None,
                   close_label: str = "Закрыть") -> Dialog:
    d = Dialog(parent, title)
    d.text(text)
    if code:
        box = QPlainTextEdit(code)
        box.setObjectName("Code")
        box.setReadOnly(True)
        box.setFixedHeight(34 + 20 * code.count("\n"))
        d.lay.addWidget(box)
        copy = QPushButton("Скопировать команду")
        copy.setObjectName("Link")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(code), copy.setText("Скопировано")))
        d.lay.addWidget(copy, 0, Qt.AlignLeft)
    for label, fn, primary in buttons or []:
        d.button(label, fn, primary)
    if close_label:
        d.button(close_label)
    d.finish()
    d.open()
    return d
