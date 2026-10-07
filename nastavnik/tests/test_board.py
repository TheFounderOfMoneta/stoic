"""Доска в окне: рисуем мышью (настоящие события), отправляем Claude, проверяем, что дошло — и до Claude,
и до базы. Поддельный claude по-настоящему читает сообщение с картинкой через stdin (stream-json)."""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from nastavnik import sketch
from nastavnik.learn import fsrs
from nastavnik.ui.board import BoardPanel, SchemaView, render_image
from nastavnik.ui.chat import AssistantMessage, UserMessage, md_blocks
from nastavnik.ui.learn_view import task_from, wants_board
from tests.test_gui import (QAPP, SHOTS, chat_text, find_button, gui, make, close, pump, send, texts,  # noqa: F401
                            topic_with_map, wait)
from tests.test_sketch import ellipse, line, rect, scribble, vee


def draw(canvas, pts) -> None:
    """Провести мышью по холсту (точки — в координатах доски)."""
    sp = [canvas.to_screen(p) for p in pts]
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp[0].x()), int(sp[0].y())))
    for q in sp[1:]:
        QTest.mouseMove(canvas, QPoint(int(q.x()), int(q.y())))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp[-1].x()), int(sp[-1].y())))
    QAPP.processEvents()


def drag(canvas, a, b, steps: int = 12) -> None:
    sa, sb = canvas.to_screen(a), canvas.to_screen(b)
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(sa.x()), int(sa.y())))
    for k in range(1, steps + 1):
        x = sa.x() + (sb.x() - sa.x()) * k / steps
        y = sa.y() + (sb.y() - sa.y()) * k / steps
        QTest.mouseMove(canvas, QPoint(int(x), int(y)))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(sb.x()), int(sb.y())))
    QAPP.processEvents()


def label_new(canvas, text: str) -> None:
    """Поле подписи открылось само после новой фигуры — печатаем и Enter."""
    assert canvas.editor.isVisible(), "после новой фигуры поле подписи должно открыться само"
    canvas.editor.setText(text)
    QTest.keyClick(canvas.editor, Qt.Key_Return)
    QAPP.processEvents()


def draw_request_schema(canvas, rng) -> None:
    """Схема «просьба → срочность → согласие» и узел с подписью от руки."""
    draw(canvas, rect(40, 40, 150, 64, rng))
    label_new(canvas, "Просьба")
    draw(canvas, rect(330, 40, 150, 64, rng))
    label_new(canvas, "Срочность")
    draw(canvas, line((150, 70), (360, 74), rng))
    draw(canvas, ellipse(405, 250, 85, 38, rng))
    canvas.editor.setText("")
    QTest.keyClick(canvas.editor, Qt.Key_Return)
    draw(canvas, scribble(370, 240, w=60, h=16, rng=rng))           # подпись от руки
    draw(canvas, line((405, 98), (405, 216), rng))


# ---------------------------------------------------------------- главное: от рисунка до ответа Claude
def test_board_drawing_reaches_claude_as_mermaid_with_picture(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    sid = c.learn_sid
    assert view.board_btn.property("suggest") in (None, False)
    send(view, "Дай задание: нарисуй схему по памяти")
    assert wait(lambda: not c.learn_busy and "Закрепим по памяти" in chat_text(view))
    # Claude просит нарисовать — кнопка доски подсвечена, но доска не открывается сама
    assert view.board_btn.property("suggest") is True and "Нарисовать на доске" in view.board_btn.text()
    assert not view.board.isVisible()
    QTest.keyClick(view.input.edit, Qt.Key_D, Qt.ControlModifier)
    pump(0.1)
    assert view.board.isVisible() and view.chat.isVisible()
    assert "нарисуй схему" in view.board.task.text()
    canvas = view.board.canvas
    draw_request_schema(canvas, random.Random(3))
    pump(0.2)
    b = canvas.board
    assert b.stats() == {"nodes": 3, "groups": 0, "edges": 2, "dangling": 0, "hand": 1, "ink": 0, "notes": 0,
                         "unnamed": 0}
    shown = view.board.mermaid.toPlainText()
    assert shown == sketch.to_mermaid(b, True)                      # видно ровно то, что уйдёт
    assert '["Просьба"]' in shown and '(["✎"])' in shown
    assert view.board.image_box.isChecked()                           # подпись от руки → картинка сама
    view.input.edit.setPlainText("кажется, так")
    view.input.conf_buttons[2].click()
    t0 = time.time()
    view.board.send_btn.click()
    assert view.board.send_btn.text() == "Claude отвечает…" and not view.board.send_btn.isEnabled()
    assert view.board_btn.property("suggest") is False               # отправили — подсказка погасла
    assert wait(lambda: not c.learn_busy and "Вижу схему" in chat_text(view))
    # Claude получил Mermaid, комментарий, уверенность и картинку
    sent = [m for m in st.messages(sid) if m["role"] == "user"][-1]
    assert sent["text"].startswith("кажется, так")
    assert "[доска]" in sent["text"] and "```mermaid\nflowchart" in sent["text"]
    assert 'n1["Просьба"]' in sent["text"] and " --> " in sent["text"]
    assert "Уверенность человека в ответе: 3/4" in sent["text"] or sent["confidence"] == 3
    assert "Вижу схему: стрелок 2 и картинку (1)" in chat_text(view)
    a = st.attempts(session_id=sid)[-1]
    assert a["phase"] == "recall" and a["correct"] == 1 and a["confidence"] == 3
    assert a["latency_ms"] > 0 and a["ts"] >= t0 - 1
    row = st.last_board(sid, sent=True)
    assert row and row["png"] and row["png"][:4] == b"\x89PNG" and row["data"]["shapes"]
    # в чате — облачко с миниатюрой схемы
    bubbles = [w for w in view.chat.findChildren(UserMessage) if w.image is not None]
    assert bubbles and "Схема с доски: 3 узла, 2 стрелки" in texts(bubbles[-1])
    assert "скорее уверен" in texts(bubbles[-1]) and "кажется, так" in texts(bubbles[-1])
    # эталон Claude в Mermaid нарисован схемой
    msg = view.chat.findChildren(AssistantMessage)[-1]
    assert msg.schemas() and {s.label for s in msg.schemas()[0].board.shapes.values()} >= {"Просьба", "Срочно?"}
    # после ответа подсказка — по правилу: просит ли Claude рисовать или идёт закрепление схемой
    arm = st.session(sid)["arms"].get("recall", "")
    expected = wants_board(view.chat.assistant_texts()[-1], st.session(sid)["step"], arm)
    assert view.board_btn.property("suggest") is expected
    # эталон — на доску (рисунок сохраняется в истории: Ctrl+Z его вернёт)
    find_button(msg, "Открыть на доске").click()
    pump(0.1)
    labels = {s.label for s in canvas.board.shapes.values()}
    assert {"Просьба", "Срочно?", "Согласие", "Проверка"} <= labels
    canvas.undo()
    assert canvas.board.stats()["hand"] == 1 and len(canvas.board.shapes) == 3
    # поправили схему: двигаем узел (стрелки тянутся следом) и дорисовываем узел со стрелкой
    canvas.set_tool("select")
    srch = next(s for s in canvas.board.shapes.values() if s.label == "Срочность")
    e = next(e for e in canvas.board.edges.values() if e.b == srch.id)
    drag(canvas, (srch.cx, srch.cy), (srch.cx + 60, srch.cy + 40))
    assert srch.x > 380
    end = canvas.board.edge_path(e)[-1]
    assert srch.contains(end, 2) and not srch.contains(end, -3)   # конец стрелки по-прежнему на краю узла
    canvas.set_tool("pen")
    rng = random.Random(9)
    draw(canvas, rect(40, 300, 150, 60, rng))
    label_new(canvas, "Согласие")
    draw(canvas, line((340, 260), (170, 320), rng))
    pump(0.2)
    view.board.send_btn.click()
    assert wait(lambda: not c.learn_busy and chat_text(view).count("Вижу схему") == 2)
    sent = [m for m in st.messages(sid) if m["role"] == "user"][-1]["text"]
    assert "Что изменилось с прошлой схемы" in sent and "«Согласие»" in sent
    assert len(st.boards(session_id=sid, sent=True)) == 2


def test_board_keyboard_and_close_keep_drawing(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    view.board_btn.click()
    canvas = view.board.canvas
    draw(canvas, rect(60, 60, 140, 60, random.Random(1)))
    label_new(canvas, "Узел")
    view.board.close_btn.click()                                      # свернули — рисунок цел
    assert not view.board.isVisible() and not view.board_btn.isChecked()
    assert st.last_board(c.learn_sid, sent=False)["data"]["shapes"][0]["label"] == "Узел"   # черновик в базе
    QTest.keyClick(view.input.edit, Qt.Key_D, Qt.ControlModifier)
    assert view.board.isVisible() and canvas.board.shapes
    view.board.wide_btn.click()                                       # во всё окно — чат прячется
    assert not view.chat.isVisible()
    view.board.wide_btn.click()
    assert view.chat.isVisible()
    # отправка схемы, пока Claude отвечает, невозможна
    c.learn_busy = True
    view.set_busy(True)
    assert not view.board.send_btn.isEnabled()
    view.set_busy(False)
    c.learn_busy = False
    assert view.board.send_btn.isEnabled()


# ---------------------------------------------------------------- инструменты доски
def panel(theme_name: str = "dark") -> BoardPanel:
    p = BoardPanel()
    p.resize(820, 720)
    p.show()
    pump(0.05)
    return p


def test_tools_undo_redo_eraser_select_and_text(gui):
    p = panel()
    cv = p.canvas
    rng = random.Random(2)
    draw(cv, rect(40, 40, 140, 60, rng))
    label_new(cv, "A")
    draw(cv, rect(360, 40, 140, 60, rng))
    label_new(cv, "B")
    draw(cv, line((150, 70), (390, 70), rng))
    b = cv.board
    assert len(b.edges) == 1
    QTest.keyClick(cv, Qt.Key_Z, Qt.ControlModifier)                  # Ctrl+Z — стрелки нет
    assert not cv.board.edges
    QTest.keyClick(cv, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert len(cv.board.edges) == 1
    # наконечник у начала отдельной «галочкой» — стрелка развернулась
    e = next(iter(cv.board.edges.values()))
    draw(cv, vee(cv.board.edge_path(e)[0], (-1, 0)))
    assert "n2 --> n1" in sketch.to_mermaid(cv.board) and not cv.board.ink
    # ластик по стрелке — стрелка стёрта, фигуры целы
    QTest.keyClick(cv, Qt.Key_3)
    assert cv.tool == "eraser" and p.tools.buttons[2].isChecked()
    mid = cv.board.edge_mid(e)
    drag(cv, (mid[0], mid[1] - 20), (mid[0], mid[1] + 20))
    assert not cv.board.edges and len(cv.board.shapes) == 2
    # текст: щелчок по узлу — его подпись; по пустому месту — надпись
    QTest.keyClick(cv, Qt.Key_4)
    a = next(s for s in cv.board.shapes.values() if s.label == "A")
    sp = cv.to_screen((a.cx, a.cy))
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp.x()), int(sp.y())))
    assert cv._editing == ("shape", a.id)
    cv.editor.setText("Атака")
    QTest.keyClick(cv.editor, Qt.Key_Return)
    assert a.label == "Атака"
    sp = cv.to_screen((260, 260))
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp.x()), int(sp.y())))
    cv.editor.setText("важно!")
    QTest.keyClick(cv.editor, Qt.Key_Return)
    assert [n.text for n in cv.board.notes.values()] == ["важно!"]
    # двойной щелчок по пустому месту — новый узел с подписью
    QTest.keyClick(cv, Qt.Key_2)
    sp = cv.to_screen((260, 420))
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp.x()), int(sp.y())))
    assert cv._editing and cv._editing[0] == "shape"
    cv.editor.setText("Новый")
    QTest.keyClick(cv.editor, Qt.Key_Return)
    assert "Новый" in {s.label for s in cv.board.shapes.values()}
    # выбор рамкой и Delete
    drag(cv, (20, 380), (520, 500))
    assert cv.sel
    QTest.keyClick(cv, Qt.Key_Delete)
    assert "Новый" not in {s.label for s in cv.board.shapes.values()}
    # конец стрелки тянем на другой узел — переподключается
    QTest.keyClick(cv, Qt.Key_1)
    draw(cv, line((150, 70), (390, 70), rng))
    e = next(iter(cv.board.edges.values()))
    c3 = cv.board.add_shape("rect", 200, 300, 120, 50, "C")
    cv.set_tool("select")
    cv.sel = {("edge", e.id)}
    end = cv.board.edge_path(e)[-1]
    drag(cv, end, (260, 325))
    assert e.b == c3 and "-->" in sketch.to_mermaid(cv.board)
    p.close()


def test_closing_editor_with_escape_and_empty_note_is_removed(gui):
    p = panel()
    cv = p.canvas
    cv.set_tool("text")
    sp = cv.to_screen((200, 200))
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp.x()), int(sp.y())))
    assert cv.editor.isVisible()
    QTest.keyClick(cv.editor, Qt.Key_Escape)
    assert not cv.editor.isVisible() and not cv.board.notes
    p.close()


def test_drawing_stays_smooth_on_a_big_board(gui):
    """Сто узлов и сто стрелок: перерисовка во время штриха не тормозит."""
    p = panel()
    cv = p.canvas
    b = sketch.Board()
    ids = [b.add_shape("rect", 30 + (k % 10) * 150, 30 + (k // 10) * 110, 120, 50, f"узел {k}") for k in range(100)]
    for k in range(99):
        b.add_edge(ids[k], ids[k + 1])
    p.load(b, fit=False)
    times = []
    pts = [(40 + k * 3, 520 + 20 * __import__("math").sin(k / 6)) for k in range(120)]
    sp = [cv.to_screen(q) for q in pts]
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp[0].x()), int(sp[0].y())))
    for q in sp[1:40]:
        QTest.mouseMove(cv, QPoint(int(q.x()), int(q.y())))
        t = time.perf_counter()
        cv.repaint()
        times.append(time.perf_counter() - t)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, QPoint(int(sp[39].x()), int(sp[39].y())))
    times.sort()
    assert times[len(times) // 2] < 0.05, f"медиана перерисовки {times[len(times) // 2] * 1000:.0f} мс"
    t = time.perf_counter()
    text = sketch.to_mermaid(cv.board)
    assert time.perf_counter() - t < 0.5 and text.count("-->") == 99
    p.close()


# ---------------------------------------------------------------- схемы Claude и картинки
def test_mermaid_in_answer_is_code_while_streaming_and_schema_when_done(gui):
    part = "Вот эталон:\n\n```mermaid\nflowchart LR\n  A[Просьба] --> B"
    assert [k for k, _ in md_blocks(part)] == ["p", "code"]
    done = part + "[Согласие]\n```\n\nСравни."
    assert [k for k, _ in md_blocks(done)] == ["p", "mermaid", "p"]
    msg = AssistantMessage(part)
    assert not msg.schemas()
    msg.set_text(done)
    assert len(msg.schemas()) == 1
    first = msg.schemas()[0]
    msg.set_text(done + " Ещё текст.")                          # схема не пересоздаётся на каждом слове
    assert msg.schemas()[0] is first
    bad = AssistantMessage("```mermaid\nsequenceDiagram\nA->>B: hi\n```")
    assert not bad.schemas() and "sequenceDiagram" in "\n".join(l.text() for l in bad.findChildren(QLabel))


def test_picture_for_claude_is_light_and_readable(gui):
    b = sketch.Board()
    rng = random.Random(4)
    for pts in (rect(40, 40, 150, 64, rng), rect(330, 40, 150, 64, rng)):
        b.add_stroke(pts)
    b.add_stroke(line((150, 70), (360, 74), rng))
    img = render_image(b)
    assert 400 < img.width() <= 1400 and img.height() > 80
    corner = img.pixelColor(2, 2)
    assert corner.lightness() > 230                          # для Claude — светлая, как бумага
    big = sketch.Board()
    big.add_shape("rect", 0, 0, 5000, 300)
    assert render_image(big).width() <= 1400                     # огромная доска — не больше 1400 px


def test_board_helpers():
    assert wants_board("Ядро.\n\nТеперь нарисуй по памяти схему: что за чем.")
    assert not wants_board("Вот схема:\n\n```mermaid\nflowchart LR\nA-->B\n```")
    assert not wants_board("Как думаешь, почему так?")
    assert not wants_board("Вот схема:\n\n```\nA -> B\n```\n\nПочему пакеты идут разными путями?")
    assert wants_board("Сравни:\n\n```mermaid\nflowchart LR\nA-->B\n```\n\nТеперь **нарисуй по памяти** свою.")
    assert wants_board("Составь схему: кто кому доверяет.")
    assert wants_board("Как думаешь, почему так?", step="recall", recall_arm="schema_recall")
    assert task_from("Хорошо.\n\nТеперь **нарисуй** схему: узлы и стрелки.\n\nПримеры любые.") \
        == "Теперь нарисуй схему: узлы и стрелки."


# ---------------------------------------------------------------- повторение
def test_schema_card_is_drawn_on_board_and_compared(gui):
    c, st, _ = gui
    tid = topic_with_map(c)
    concept = st.concepts(tid)[0]
    iid = st.add_item(tid, concept["id"], "Нарисуй по памяти: как работает просьба мошенника",
                      "flowchart LR\n  A[Просьба] --> B{Срочно?}\n  B -->|да| C[Согласие]", kind="schema")
    st.update_item(iid, due=time.time() - 60, reps=1, stability=3, difficulty=5, last_review=time.time() - 4 * 86400)
    c.window.open_review(tid)
    pump(0.2)
    page = c.window.review_page
    assert page.current["id"] == iid and page.draw_btn.isVisible()
    page.draw_btn.click()
    pump(0.1)
    assert page.board.isVisible() and not page.attempt.isVisible() and not page.reveal_btn.isVisible()
    assert page.board.mermaid_btn.text().startswith("Схема текстом")
    cv = page.board.canvas
    QTest.keyClick(cv, Qt.Key_2)                                   # «2» — инструмент доски, а не оценка
    assert cv.tool == "select" and page.current["id"] == iid and not page.revealed
    QTest.keyClick(cv, Qt.Key_1)
    rng = random.Random(6)
    draw(cv, rect(40, 40, 150, 64, rng))
    label_new(cv, "Просьба")
    draw(cv, rect(330, 40, 150, 64, rng))
    label_new(cv, "Согласие")
    draw(cv, line((150, 70), (360, 74), rng))
    pump(0.9)
    c.window.grab().save(str(SHOTS / "board-review-dark.png"))
    find_button(page.board, "Готово — показать ответ").click()
    pump(0.2)
    assert page.revealed and page.grades.isVisible() and not page.board.isVisible()
    views = page.schemas.findChildren(SchemaView)
    assert len(views) == 2                                          # ваша схема и эталон — обе картинками
    assert "ВАША СХЕМА · 2 УЗЛА, 1 СТРЕЛКА" in texts(page.schemas) and "ЭТАЛОН" in texts(page.schemas)
    assert not page.answer.isVisible()                              # эталон-схема вместо текста Mermaid
    rows = st.boards(item_id=iid)
    assert rows and rows[0]["sent"] == 1 and rows[0]["data"]["shapes"]
    QTest.keyClick(page.grade_buttons[fsrs.GOOD], Qt.Key_3)         # оценки снова на клавишах
    pump(0.2)
    assert st.item(iid)["reps"] == 2


# ---------------------------------------------------------------- PRIMARY: щелчок не выделяет
def _mouse(widget, kind, pos, buttons):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    btn = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    ev = QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), btn, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def jitter_click(widget, x, y, dx=3, dy=1) -> None:
    """Щелчок, во время которого рука дрогнула на несколько пикселей (на тачпаде — почти всегда)."""
    from PySide6.QtCore import QEvent
    _mouse(widget, QEvent.MouseButtonPress, QPoint(x, y), Qt.LeftButton)
    _mouse(widget, QEvent.MouseMove, QPoint(x + dx, y + dy), Qt.LeftButton)
    _mouse(widget, QEvent.MouseButtonRelease, QPoint(x + dx, y + dy), Qt.NoButton)
    QAPP.processEvents()


def press_drag(widget, a, b) -> None:
    from PySide6.QtCore import QEvent
    _mouse(widget, QEvent.MouseButtonPress, QPoint(*a), Qt.LeftButton)
    for k in range(1, 11):
        _mouse(widget, QEvent.MouseMove, QPoint(a[0] + (b[0] - a[0]) * k // 10, a[1]), Qt.LeftButton)
    _mouse(widget, QEvent.MouseButtonRelease, QPoint(*b), Qt.NoButton)
    QAPP.processEvents()


def test_click_with_hand_jitter_does_not_select_text(gui):
    """На X11 до исправления: сдвиг на 2–3 px при щелчке выделял букву, и она уходила в PRIMARY
    («и», «р», «м») — Aqua включала правку при «0 слов выделено»."""
    c, st, _ = gui
    tid = topic_with_map(c)
    c.start_learning(tid)
    view = c.window.session_view
    assert wait(lambda: not c.learn_busy and "Крючок" in chat_text(view))
    view.chat.add_user("Наверное, пакеты идут разными путями", 3)
    pump(0.2)
    body = [l for l in view.chat.findChildren(QLabel) if l.objectName() == "Body" and l.isVisible()][0]
    bubble = view.chat.findChildren(UserMessage)[0].label
    for x in (20, 60, 100, 140, 200):
        jitter_click(body, x, 10)
        assert body.selectedText() == "", f"щелчок в {x} px выделил «{body.selectedText()}»"
    for x in (10, 40, 80):
        jitter_click(bubble, x, 8, dx=2)
        assert bubble.selectedText() == ""
    view.input.edit.setPlainText("мой ответ про пакеты")
    for x in (20, 60, 100):
        jitter_click(view.input.edit.viewport(), x, 15)
        assert not view.input.edit.textCursor().hasSelection()
    view.board_btn.click()
    cv = view.board.canvas
    sid = cv.board.add_shape("rect", 40, 40, 140, 60, "Подпись узла")
    cv.edit_label("shape", sid)
    assert not cv.editor.hasSelectedText()                 # поле подписи открывается без выделения
    jitter_click(cv.editor, 30, 12)
    assert not cv.editor.hasSelectedText()
    cv.commit_edit()
    # настоящее выделение протяжкой работает как раньше
    press_drag(body, (5, 10), (160, 10))
    assert len(body.selectedText()) >= 8
