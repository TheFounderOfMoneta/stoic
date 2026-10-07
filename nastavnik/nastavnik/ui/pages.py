"""Страницы: Главная, Прогресс, Настройки (в стиле «Системных настроек» Aqua).

Главная отвечает на один вопрос — «что сейчас?»: где остановились (петля как крючок), план на
сегодня и одна главная кнопка. Проблемы — баннером с кнопкой, которая их исправляет.
"""
from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QRectF, QTime, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QTimeEdit, QVBoxLayout, QWidget)

from ..learn import bandit
from ..util import ahead_text, minutes_text, plural
from . import widgets as W
from .look import CONTENT_MAX_W

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class Page:
    """Прокручиваемая страница с колонкой по центру и заголовком с «?» (как в Aqua)."""

    def __init__(self, title: str, subtitle: str = "", scroll: bool = True):
        self.area = QScrollArea() if scroll else QWidget()
        outer = QWidget()
        row = QHBoxLayout(outer)
        row.setContentsMargins(36, 30, 36, 30)
        self.column = QWidget()
        self.column.setMaximumWidth(CONTENT_MAX_W)
        self.lay = QVBoxLayout(self.column)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(18)
        row.addStretch(1)
        row.addWidget(self.column, 100)
        row.addStretch(1)
        head = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        texts.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        texts.addWidget(self.subtitle)
        head.addLayout(texts, 1)
        self.head_right = QHBoxLayout()
        head.addLayout(self.head_right)
        self.help = W.HelpButton()
        head.addWidget(self.help, 0, Qt.AlignTop)
        self.lay.addLayout(head)
        if scroll:
            self.area.setWidgetResizable(True)
            self.area.setFrameShape(QFrame.NoFrame)
            self.area.setWidget(outer)
        else:
            lay = QVBoxLayout(self.area)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(outer)
        self.body = QVBoxLayout()
        self.body.setSpacing(18)
        self.lay.addLayout(self.body)
        self.lay.addStretch(1)

    def clear_body(self) -> None:
        clear_layout(self.body)


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()            # сразу убрать с экрана: удаление произойдёт позже
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, fn, primary: bool = False, name: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name or ("Primary" if primary else ""))
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(lambda _=False: fn())
    return b


def bar(value: float, height: int = 6) -> QProgressBar:
    pb = QProgressBar()
    pb.setObjectName("Thin")
    pb.setTextVisible(False)
    pb.setFixedHeight(height)
    pb.setRange(0, 1000)
    pb.setValue(int(max(0.0, min(1.0, value)) * 1000))
    return pb


MASTERY_TIP = ("Пройдено — понятие разобрали в сессии, засчитывается сразу.\n"
               "Закреплено — вы его помните через время: все карточки держатся от трёх недель "
               "и проверка через несколько дней сдана. Закрепляет «Повторение».")


class ProgressBar(QWidget):
    """Прогресс темы в два цвета: светлое — пройдено, сплошное зелёное — закреплено."""

    def __init__(self, studied: float, mastered: float, height: int = 6):
        super().__init__()
        self.studied = max(0.0, min(1.0, studied))
        self.mastered = max(0.0, min(self.studied, mastered))
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(MASTERY_TIP)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setBrush(W.pc("switch_off"))
        p.drawRoundedRect(r, rad, rad)
        for frac, color in ((self.studied, W.pc("accent")), (self.mastered, W.pc("success"))):
            if frac > 0:
                if color == W.pc("accent"):
                    color.setAlpha(150)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * frac), r.height()), rad, rad)
        p.end()


def topic_bar(p: dict) -> ProgressBar:
    total = max(1, p["total"])
    return ProgressBar(p["studied"] / total, p["mastered"] / total)


def progress_text(p: dict, with_reviews: bool = True) -> str:
    """«Пройдено 1 из 12 · закреплено 0 · следующее повторение через 3 дня»."""
    parts = [f"Пройдено {p['studied']} из {p['total']}", f"закреплено {p['mastered']}"]
    if with_reviews:
        if p.get("due"):
            parts.append(f"повторений сегодня {p['due']}")
        elif p.get("next_review"):
            parts.append(f"следующее повторение {ahead_text(p['next_review'])}")
    return " · ".join(parts)


def greeting(ts: float | None = None) -> str:
    h = time.localtime(ts).tm_hour if ts else time.localtime().tm_hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 18:
        return "Добрый день"
    if 18 <= h < 23:
        return "Добрый вечер"
    return "Доброй ночи"


# ================================================================= Главная
class HomePage(Page):
    def __init__(self, controller):
        super().__init__("Главная", "")
        self.c = controller
        self.notices = QVBoxLayout()
        self.notices.setSpacing(8)
        self.lay.insertLayout(1, self.notices)

    def render_notices(self, notices: dict) -> None:
        clear_layout(self.notices)
        for _key, (kind, text, btn_label, fn) in notices.items():
            row = QHBoxLayout()
            banner = label(text, "BannerError" if kind == "error" else "Banner")
            row.addWidget(banner, 1)
            if btn_label and fn:
                row.addWidget(button(btn_label, fn, primary=True), 0, Qt.AlignVCenter)
            self.notices.addLayout(row)

    def refresh(self) -> None:
        d = self.c.home_data()
        today = dt.date.today()
        name = self.c.settings.get("profile.name", "")
        self.title.setText(greeting() + (f", {name}" if name else ""))
        self.subtitle.setText(f"{WEEKDAYS[today.weekday()].capitalize()}, {today.day} {MONTHS[today.month - 1]}")
        self.clear_body()
        if not d["topics"]:
            card = W.Card("Начните с темы", "Напишите, что хотите изучить и зачем. Claude составит карту темы, "
                          "а дальше каждая сессия будет подстраиваться под вас: под темп, формат и то, "
                          "что даётся трудно.")
            card.add(button("Новая тема…", self.c.new_topic_dialog, primary=True))
            self.body.addWidget(card)
            self._talk_card()
            return
        plan = d["plan"]
        card = W.Card("", "", padding=20)
        head = label("СЕГОДНЯ", "SectionLabel")
        card.add(head)
        if plan.open_loop:
            card.add(label("Вы остановились на вопросе:", "Muted"))
            card.add(label(f"«{plan.open_loop}»", "Loop"))
        topic = d["topic"]
        card.add(label(f"{topic['title']}: {plan.summary()}", "Big"))
        if plan.note:
            card.add(label(plan.note, "Muted"))
        row = QHBoxLayout()
        start_text = {"return": "Вернуться на 5 минут", "short": "Короткая сессия"}.get(plan.kind, "Начать сессию")
        self.start_btn = button(start_text, lambda: self.c.start_learning(topic["id"]), primary=True)
        row.addWidget(self.start_btn)
        if d["due_all"]:
            row.addWidget(button(f"Только повторение ({d['due_all']})", lambda: self.c.window.open_page("review")))
        row.addStretch(1)
        card.add_layout(row)
        self.body.addWidget(card)
        stats = QHBoxLayout()
        stats.setSpacing(10)
        for value, caption in (
                (f"{d['streak']['days']}", f"{plural(d['streak']['days'], ('день', 'дня', 'дней'))} подряд"
                 + (" · заморозка" if d["streak"]["frozen"] else "")),
                (f"{round(d['week_min'])}", f"мин за неделю из {d['week_goal']}"),
                (f"{round(d['retention'] * 100)} %" if d["retention"] is not None else "—", "прогноз удержания"),
                (f"{d['studied']}/{d['concepts']}", f"понятий пройдено · закреплено {d['mastered']}")):
            tile = W.Card("", "", padding=14)
            t = W.StatTile(caption)
            t.set(value)
            tile.add(t)
            stats.addWidget(tile, 1)
        self.body.addLayout(stats)
        if d["next_due"]:
            self.body.addWidget(label(d["next_due"], "Muted"))
        others = [t for t in d["topics"] if t["id"] != topic["id"]]
        if others:
            g = W.Group("Другие темы")
            for t in others[:4]:
                g.add_row(t["title"], t["goal"], button("Открыть", lambda tid=t["id"]: self.c.window.open_topic(tid)))
            self.body.addWidget(g)
        self._talk_card()

    def _talk_card(self) -> None:
        g = W.Group("Разговор")
        g.add_row("Выговориться", "Отдельно от учёбы: без оценок, серий и напоминаний. Переписка не хранится.",
                  button("Открыть", lambda: self.c.window.open_page("talk")))
        self.body.addWidget(g)


# ================================================================= Прогресс
class ProgressPage(Page):
    def __init__(self, controller):
        super().__init__("Прогресс", "Что о вас узнала система — и на чём это основано")
        self.c = controller

    def refresh(self) -> None:
        d = self.c.progress_data()
        self.clear_body()
        # --- время
        g = W.Group("Время", "Активное время: только пока окно открыто и вы что-то делаете (пауза — после минуты "
                             "без мыши и клавиатуры).")
        week = QWidget()
        wl = QVBoxLayout(week)
        wl.setContentsMargins(0, 10, 0, 10)
        wl.addWidget(label(f"За неделю: {minutes_text(d['week_min'])} из {minutes_text(d['week_goal'])}"))
        wl.addWidget(bar(d["week_min"] / max(1, d["week_goal"])))
        g.add_widget(week)
        s = d["streak"]
        frozen = ", ".join(f"{d[8:10]}.{d[5:7]}" for d in s["frozen"])
        g.add_row("Серия", f"{s['days']} {plural(s['days'], ('день', 'дня', 'дней'))} подряд"
                  + (f" · заморозка закрыла пропуск ({frozen})" if s["frozen"] else ""))
        hours = ", ".join(f"{h}:00" for h in d["best_hours"]) or "пока не видно"
        g.add_row("Когда вы садитесь сами", hours)
        fat = d["fatigue"]
        g.add_row("Длина сессии", f"После {fat} мин точность обычно падает ниже 70 % — дальше план не заходит."
                  if fat else "Точка усталости пока не видна — план держится вашей длины сессии.")
        self.body.addWidget(g)
        # --- память
        g = W.Group("Память", "Повторения по FSRS: карточка возвращается, когда вероятность вспомнить падает "
                              "до 90 %.")
        r = d["retention"]
        g.add_row("Прогноз удержания", f"{round(r * 100)} % карточек вы сейчас вспомните" if r is not None
                  else "Появится после первых карточек")
        k, n = d["memory_factor"], d["memory_n"]
        if n >= 100:
            text = (f"×{k:.2f}: вы помните дольше среднего — интервалы растянуты" if k > 1.05 else
                    f"×{k:.2f}: интервалы чуть короче средних, чтобы не забывать" if k < 0.95 else
                    f"×{k:.2f}: ваша память близка к средней")
            text += f" (по {n} повторениям)"
        else:
            text = f"Пока средние параметры. Личный множитель — после 100 повторений с перерывом (сейчас {n})."
        g.add_row("Ваша память", text)
        g.add_row("Карточек в повторении", str(d["cards"]))
        self.body.addWidget(g)
        # --- что работает
        g = W.Group("Что работает на вас",
                    "Форматы проверяются результатом, а не ощущением: порядок, подача и закрепление — отложенным "
                    "тестом через несколько дней, крючок и подарок — вашей оценкой сессии.")
        for row in d["formats"]:
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 10, 0, 10)
            bl.setSpacing(6)
            head = f"{row['title']}" + (f" · {row['context']}" if row["context"] else "")
            bl.addWidget(label(head))
            if row["total"] < 10:
                bl.addWidget(label(f"Пока мало данных — проверок: {row['total']}. Варианты чередуются.", "Muted"))
            ranked = sorted(row["arms"], key=lambda a: -a["mean"])
            # «лучше всего» — только при заметном отрыве: равные доли не делают победителя
            best = ranked[0] if row["total"] >= 10 and ranked[0]["mean"] - ranked[1]["mean"] >= 0.05 else None
            for a in row["arms"]:
                line = QHBoxLayout()
                name = label(a["label"] + ("  ← лучше всего" if best is a else ""), "Soft", wrap=False)
                name.setMinimumWidth(260)
                line.addWidget(name)
                line.addWidget(bar(a["mean"] if a["n"] else 0), 1)
                line.addWidget(label(f"{round(a['mean'] * 100)} % · {a['n']}" if a["n"] else "—", "Muted",
                                     wrap=False))
                bl.addLayout(line)
            g.add_widget(box)
        self.body.addWidget(g)
        # --- мотивация
        m = d["motivation"]
        g = W.Group("Мотивация", "Тяга, удовольствие и польза считаются отдельно: тяга может расти без удовольствия "
                                 "и пользы — так работают ленты. Сравнение: последние 14 дней и 14 дней до них.")
        if m["alarm"]:
            g.add_widget(label("Тяга растёт, а удовольствие и польза — нет. Похоже, сессии затягивают сами по себе. "
                               "Система уже урезала переменные награды; можно на неделю убрать подарки совсем.",
                               "BannerError"))
        cur, prev = m["current"], m["previous"]

        def fmt(v, kind):
            if v is None:
                return "—"
            return f"{round(v * 100)} %" if kind == "share" else f"{v:.1f}"
        for title, key, kind, hint in (("Тяга", "pull", "share", "доля сессий, начатых без напоминания"),
                                       ("Удовольствие", "liking", "num", "средняя оценка сессии из 5"),
                                       ("Польза", "use", "share", "верно на отложенных тестах")):
            arrow = ""
            if cur[key] is not None and prev[key] is not None:
                diff = cur[key] - prev[key]
                arrow = " ↑" if diff > 0.02 else " ↓" if diff < -0.02 else " →"
            g.add_row(title, hint, label(f"{fmt(cur[key], kind)}{arrow}  (было {fmt(prev[key], kind)})", "Soft",
                                         wrap=False))
        if not m["enough"]:
            g.add_widget(label("Сравнение станет надёжным, когда в каждом из двух периодов будет хотя бы 4 сессии.",
                               "Muted"))
        self.body.addWidget(g)
        # --- темы
        if d["topics"]:
            g = W.Group("Темы")
            for t in d["topics"]:
                box = QWidget()
                bl = QVBoxLayout(box)
                bl.setContentsMargins(0, 10, 0, 10)
                p = t["progress"]
                bl.addWidget(label(f"{t['title']} — {progress_text(p, with_reviews=False).lower()}"))
                bl.addWidget(topic_bar(p))
                g.add_widget(box)
            self.body.addWidget(g)


# ================================================================= Настройки
class SettingsPage(Page):
    def __init__(self, controller):
        super().__init__("Настройки", "Почти всё подстраивается само — здесь только то, что решаете вы")
        self.c = controller

    def rebuild(self) -> None:
        s = self.c.settings
        self.clear_body()
        g = W.Group("О вас", "Из интересов Claude берёт примеры, крючки и подарки.")
        g.add_row("Как к вам обращаться", "", W.bind_line(s, "profile.name", "Имя"))
        g.add_row("Интересы", "Через запятую: игры, свой проект, музыка…",
                  W.bind_line(s, "profile.interests", "Чем вы увлекаетесь"))
        self.body.addWidget(g)
        g = W.Group("Учёба")
        g.add_row("Длина сессии", "Если точность к концу падает, план сам станет короче",
                  W.bind_spin(s, "learn.session_minutes", 5, 120, " мин"))
        g.add_row("Цель в неделю", "", W.bind_spin(s, "learn.week_goal_minutes", 10, 3000, " мин", 10))
        g.add_row("Новых понятий за сессию", "", W.bind_spin(s, "learn.new_per_session", 0, 5))
        self.body.addWidget(g)
        g = W.Group("Напоминание", "Приходит, только если сегодня ещё не занимались. Если ПК был выключен — "
                                   "сразу после включения.")
        g.add_row("Напоминать", "", W.bind_switch(s, "reminder.enabled", lambda _v: self.c.apply_timer()))
        te = QTimeEdit(QTime.fromString(s.get("reminder.time", "19:07"), "HH:mm"))
        te.setDisplayFormat("HH:mm")
        te.setFixedWidth(110)

        def time_changed(t):
            s.set("reminder.time", t.toString("HH:mm"))
            self.c.apply_timer()
        te.timeChanged.connect(time_changed)
        g.add_row("Время", "Не ровный час — так надёжнее", te)
        self.body.addWidget(g)
        g = W.Group("Вид")
        g.add_row("Тема", "", W.bind_combo(s, "ui.theme", [("auto", "Как в системе"), ("dark", "Тёмная"),
                                                           ("light", "Светлая")], lambda _v: self.c.window.apply_theme()))
        g.add_row("Размер текста", "", W.bind_combo(s, "ui.text_size", [("s", "Мельче"), ("m", "Обычный"),
                                                                       ("l", "Крупнее")],
                                                   lambda _v: self.c.window.apply_theme()))
        self.body.addWidget(g)
        g = W.Group("Claude", "Через Claude Code по вашей подписке. Ключ API не нужен.")
        st = self.c.claude_status
        status = "Вход выполнен" if st.get("ok") else (st.get("message") or "Проверяю…")
        row_btn = button("Войти в Claude" if not st.get("ok") else "Проверить", self.c.login_claude if not st.get("ok")
                         else lambda: self.c.check_claude(quiet=False))
        g.add_row("Состояние", status, row_btn)
        g.add_row("Модель", "Sonnet — быстро и бережёт лимиты; Opus — глубже",
                  W.bind_combo(s, "claude.model", [("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")]))
        self.body.addWidget(g)
        g = W.Group("Разговор")
        g.add_row("Хранить переписку", "По умолчанию разговор удаляется после завершения — и у вас, и в журнале "
                                       "Claude Code", W.bind_switch(s, "talk.keep_transcripts"))
        g.add_row("Память", "Короткие сводки прошлых разговоров",
                  W.bind_combo(s, "talk.memory", [("ask", "Спрашивать каждый раз"), ("always", "Запоминать"),
                                                  ("never", "Не запоминать")]))
        self.body.addWidget(g)
        sec = W.Section("Дополнительно", "Контекст Claude, эксперименты, данные")
        sec.add_row("Новый разговор с Claude после", "Состояние темы не теряется: оно в базе и передаётся в начало",
                    W.bind_spin(s, "learn.context_tokens", 8000, 400000, " ток.", 1000))
        explore = W.bind_combo(s, "learn.explore_share", [(0.0, "Нет"), (0.1, "10 %"), (0.15, "15 %"),
                                                           (0.25, "25 %")])
        sec.add_row("Разведка форматов", "Доля сессий с вариантом, который пробовали реже", explore)
        sec.add_row("Сбросить эксперименты", "Форматы начнут подбираться заново",
                    button("Сбросить", self.c.reset_experiments, name="Danger"))
        sec.add_row("Экспорт", "Всё об учёбе в одном JSON", button("Сохранить…", self._export))
        sec.add_row("Данные", str(self.c.data_dir()), button("Открыть папку", self.c.open_data_dir))
        self.body.addWidget(sec)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self.area, "Экспорт", "nastavnik.json", "JSON (*.json)")
        if path:
            self.c.export(path)
