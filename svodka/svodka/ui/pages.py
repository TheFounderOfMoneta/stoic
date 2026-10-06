"""Страницы: Поиск, Сохранённое, Настройки (в стиле «Системных настроек» Aqua)."""
from __future__ import annotations

import json
import re
import time

from PySide6.QtCore import QTime, Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QTimeEdit, QVBoxLayout, QWidget)

from .. import search as libsearch
from ..rank.calibrate import quality
from ..util import jload
from . import widgets as W
from .feed_view import FeedList, item_rows
from .look import CONTENT_MAX_W


class Page:
    """Прокручиваемая страница с колонкой по центру и заголовком с «?» (как в Aqua)."""

    def __init__(self, title: str, subtitle: str = "", scroll: bool = True):
        self.area = QScrollArea() if scroll else QWidget()
        outer = QWidget()
        row = QHBoxLayout(outer)
        row.setContentsMargins(36, 30, 36, 24)
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
        t = QLabel(title)
        t.setObjectName("PageTitle")
        texts.addWidget(t)
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


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


class SearchPage(Page):
    def __init__(self, controller):
        super().__init__("Поиск", "По вашей библиотеке и, если нужно, по интернету", scroll=False)
        self.c = controller
        self.box = QLineEdit()
        self.box.setObjectName("SearchBox")
        self.box.setPlaceholderText("Что найти? Например: регулирование ИИ в США")
        self.box.returnPressed.connect(self.run_local)
        self._debounce = QTimer(self.area)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self.run_local)
        self.box.textChanged.connect(lambda _t: self._debounce.start())
        self.lay.addWidget(self.box)
        row = QHBoxLayout()
        self.hint = QLabel("Ищу по переводам и оригиналам, с учётом окончаний: «нейросетей» найдёт «нейросеть».")
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)
        row.addWidget(self.hint, 1)
        self.web = QPushButton("  Искать в интернете с Claude")
        self.web.setObjectName("Primary")
        self.web.setCursor(Qt.PointingHandCursor)
        self.web.clicked.connect(self.run_web)
        row.addWidget(self.web)
        self.lay.addLayout(row)
        self.save_row = QHBoxLayout()
        self.save_switch = W.Switch()
        self.save_switch.toggled.connect(self._save_query)
        lab = QLabel("Проверять этот запрос при каждом сборе")
        lab.setObjectName("Muted")
        self.save_row.addWidget(self.save_switch)
        self.save_row.addWidget(lab)
        self.save_row.addStretch(1)
        self.lay.addLayout(self.save_row)
        self.list = FeedList()
        self.list.open_article.connect(self.c.open_article)
        self.list.action.connect(self.c.feed_action)
        self.list.on_impression = lambda aid, pos, ms, it: self.c.storage.log_impression(aid, pos, ms, "search")
        self.lay.addWidget(self.list, 1)
        self.saved_box = QLabel("")
        self.saved_box.setObjectName("Muted")
        self.saved_box.setWordWrap(True)
        self.lay.addWidget(self.saved_box)
        self.refresh_saved()

    def refresh_saved(self) -> None:
        qs = [q["text"] for q in self.c.storage.saved_queries()]
        self.saved_box.setText(("Сохранённые запросы: " + " · ".join(f"«{q}»" for q in qs)) if qs else "")

    def _save_query(self, on: bool) -> None:
        q = self.box.text().strip()
        if not q:
            return
        if on:
            self.c.storage.remember_query(q, saved=True)
        else:
            self.c.storage.unsave_query(q)
        self.refresh_saved()

    def run_local(self) -> None:
        q = self.box.text().strip()
        saved = {x["text"] for x in self.c.storage.saved_queries()}
        self.save_switch.blockSignals(True)
        self.save_switch.setChecked(q in saved)
        self.save_switch.blockSignals(False)
        if len(q) < 2:
            self.list.set_rows([])
            return
        ranker = self.c.ranker()
        results = libsearch.search(self.c.storage, q, ranker=ranker)
        items = ranker.score_many([r["article"] for r in results], sample=False) if results else []
        for it, r in zip(items, results):
            it.article = dict(it.article)
            it.article["summary_ru"] = [strip_tags(r["snippet"])] if r["snippet"] else it.article["summary_ru"]
        self.list.set_rows(item_rows(items, big=True, title=f"В БИБЛИОТЕКЕ: {len(items)}" if items else ""))
        self.hint.setText("Ничего не нашлось в библиотеке — Claude поищет в интернете, переведёт и добавит сюда."
                          if not items else "Нужно свежее? Claude поищет в интернете (около минуты).")

    def run_web(self) -> None:
        q = self.box.text().strip()
        if len(q) < 2:
            self.box.setFocus()
            return
        self.c.storage.log_event(None, "search", meta=q)
        self.web.setEnabled(False)
        self.hint.setText("Claude ищет…")

        def done(res: dict):
            self.web.setEnabled(True)
            self.run_local()
            if res.get("saved") and res.get("run_id"):
                self.show_found(res["run_id"])
            self.hint.setText(res.get("message", ""))
        self.c.web_search(q, lambda m: self.hint.setText(m), done)

    def show_found(self, run_id: int) -> None:
        """Найденное Claude — сверху, остальное из библиотеки — ниже."""
        found = self.c.storage.articles("run_id=?", (run_id,))
        if not found:
            return
        ranker = self.c.ranker()
        new_items = ranker.score_many(found, sample=False)
        new_ids = {it.id for it in new_items}
        rest = [r["item"] for r in self.list.rows() if r["kind"] == "item" and r["item"].id not in new_ids]
        rows = item_rows(new_items, big=True, title=f"НАЙДЕНО СЕЙЧАС: {len(new_items)}")
        if rest:
            rows += item_rows(rest, big=True, title="В БИБЛИОТЕКЕ")
        self.list.set_rows(rows)


class SavedPage(Page):
    def __init__(self, controller):
        super().__init__("Сохранённое", "Закладки не удаляются автоматически", scroll=False)
        self.c = controller
        self.list = FeedList()
        self.list.open_article.connect(self.c.open_article)
        self.list.action.connect(self.c.feed_action)
        self.empty = QLabel("Здесь будут статьи, которые вы сохраните — кнопка «Сохранить» в ленте или в статье.")
        self.empty.setObjectName("Hint")
        self.lay.addWidget(self.empty)
        self.lay.addWidget(self.list, 1)

    def refresh(self) -> None:
        rows = self.c.storage.articles("saved=1", order="collected_at DESC")
        items = self.c.ranker().score_many(rows, sample=False) if rows else []
        self.list.set_rows(item_rows(items, big=True))
        self.empty.setVisible(not items)


WEIGHT_STEPS = [(0.5, "Реже"), (1.0, "Обычно"), (1.5, "Чаще")]


class SettingsPage(Page):
    def __init__(self, controller):
        super().__init__("Настройки", "Почти всё лента решает сама — здесь только главное")
        self.c = controller
        self.body = QVBoxLayout()
        self.body.setSpacing(22)
        self.lay.addLayout(self.body)
        self.lay.addStretch(1)
        self.rebuild()

    def rebuild(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        st = self.c.storage
        self._proposals()
        # --- интересы
        g = W.Group("Интересы", "Темы — отправная точка. Дальше лента учится на том, что вы читаете.")
        for t in st.topics(enabled_only=False):
            g.add_widget(self._topic_row(t))
        add = QWidget()
        al = QHBoxLayout(add)
        al.setContentsMargins(0, 10, 0, 10)
        name = QLineEdit()
        name.setPlaceholderText("Новая тема")
        desc = QLineEdit()
        desc.setPlaceholderText("Что именно искать (необязательно)")
        btn = QPushButton("Добавить")
        btn.setCursor(Qt.PointingHandCursor)

        def add_topic():
            if name.text().strip():
                st.upsert_topic(name.text().strip(), desc.text().strip(), 1.0)
                self.rebuild()
        btn.clicked.connect(add_topic)
        al.addWidget(name, 1)
        al.addWidget(desc, 2)
        al.addWidget(btn)
        g.add_widget(add)
        self.body.addWidget(g)
        # --- профиль
        g = W.Group("Профиль", "Описание ваших интересов своими словами — его читает Claude при каждом сборе.")
        edit = QPlainTextEdit(st.profile_text())
        edit.setMinimumHeight(110)
        save = QPushButton("Сохранить профиль")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(lambda: (st.set_profile(edit.toPlainText().strip()), save.setText("Сохранено")))
        g.add_widget(edit)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 6, 0, 6)
        rl.addStretch(1)
        rl.addWidget(save)
        g.add_widget(row)
        self.body.addWidget(g)
        # --- сбор
        g = W.Group("Сбор", self.c.collect_status_text())
        sw = W.bind_switch(self.c.settings, "schedule.enabled", lambda _v: self.c.reinstall_timer())
        g.add_row("Собирать по расписанию", "Утром и вечером; если ПК был выключен — сразу после включения", sw)
        times = QWidget()
        tl = QHBoxLayout(times)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(8)
        edits = []
        for t in (self.c.settings.get("schedule.times") or ["07:37", "18:37"])[:2]:
            te = QTimeEdit(QTime.fromString(t, "HH:mm"))
            te.setDisplayFormat("HH:mm")
            te.setFixedWidth(80)
            edits.append(te)
            tl.addWidget(te)

        def times_changed():
            vals = sorted({e.time().toString("HH:mm") for e in edits})
            self.c.settings.set("schedule.times", vals)
            self.c.reinstall_timer()
        for e in edits:
            e.editingFinished.connect(times_changed)
        g.add_row("Время сборов", "Лучше не ровный час — так меньше задержек", times)
        now_btn = QPushButton("Собрать сейчас")
        now_btn.setCursor(Qt.PointingHandCursor)
        now_btn.clicked.connect(lambda: self.c.collect_now())
        g.add_row("Сбор вручную", "Обычно не нужен — лента обновляется сама", now_btn)
        status = self.c.claude_status
        login = QPushButton("Войти в Claude" if not status.get("ok") else "Проверить")
        login.setCursor(Qt.PointingHandCursor)
        login.clicked.connect(self.c.login_claude if not status.get("ok") else self.c.check_claude)
        g.add_row("Claude", "Вход выполнен, работает по подписке" if status.get("ok")
                  else (status.get("message") or "Проверяю…"), login)
        self.body.addWidget(g)
        # --- вид
        g = W.Group("Внешний вид")
        g.add_row("Тема", "Как в системе, тёмная или светлая", W.bind_combo(
            self.c.settings, "ui.theme", [("auto", "Как в системе"), ("dark", "Тёмная"), ("light", "Светлая")],
            lambda _v: self.c.apply_theme()))
        seg = W.Segmented(["Мельче", "Обычно", "Крупнее"])
        sizes = ["s", "m", "l"]
        seg.buttons[sizes.index(self.c.settings.get("ui.text_size", "m"))].setChecked(True)
        seg.changed.connect(lambda i: (self.c.settings.set("ui.text_size", sizes[i]), self.c.apply_theme()))
        g.add_row("Размер текста статей", "Шрифт Literata — для долгого чтения", seg)
        g.add_row("Запускать при входе в систему", "Чтобы лента и уведомления были под рукой",
                  W.bind_switch(self.c.settings, "ui.autostart", lambda v: self.c.set_autostart(v)))
        self.body.addWidget(g)
        # --- дополнительно
        self.body.addWidget(self._learning())
        self.body.addWidget(self._translation())
        self.body.addWidget(self._storage())
        self.body.addWidget(self._journal())

    # ------------------------------------------------------------- блоки
    def _topic_row(self, t: dict) -> QWidget:
        st = self.c.storage
        row = W.SettingRow(t["name"], t["description"], None)
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        seg = W.Segmented([label for _w, label in WEIGHT_STEPS])
        idx = min(range(3), key=lambda i: abs(WEIGHT_STEPS[i][0] - float(t["weight"])))
        seg.buttons[idx].setChecked(True)
        seg.changed.connect(lambda i, t=t: st.upsert_topic(t["name"], t["description"], WEIGHT_STEPS[i][0],
                                                           t["include_words"], t["exclude_words"], bool(t["enabled"])))
        sw = W.Switch()
        sw.setChecked(bool(t["enabled"]))
        sw.toggled.connect(lambda on, t=t: st.upsert_topic(t["name"], t["description"], t["weight"],
                                                           t["include_words"], t["exclude_words"], on))
        rm = QPushButton("✕")
        rm.setObjectName("Icon")
        rm.setToolTip("Удалить тему")
        rm.setCursor(Qt.PointingHandCursor)
        rm.clicked.connect(lambda _=False, n=t["name"]: (st.delete_topic(n), self.rebuild()))
        cl.addWidget(seg)
        cl.addWidget(sw)
        cl.addWidget(rm)
        row.layout().addWidget(controls, 0, Qt.AlignVCenter | Qt.AlignRight)
        return row

    def _proposals(self) -> None:
        st = self.c.storage
        prop = st.proposed_profile()
        weekly = jload(st.meta_get("weekly_suggestions", ""), {})
        topics = jload(st.meta_get("topic_suggestions", ""), [])
        if not (prop or weekly.get("summary") or topics):
            return
        card = W.Card("Claude предлагает", "Ничего не меняется без вас.")
        if weekly.get("summary"):
            s = QLabel("Что понял за неделю: " + weekly["summary"])
            s.setWordWrap(True)
            card.add(s)
        if prop:
            card.add(self._muted(f"Новый профиль ({prop.get('note') or 'обновление'}):"))
            new = QLabel(prop["text"])
            new.setWordWrap(True)
            new.setObjectName("Soft")
            card.add(new)
            card.add(self._muted("Было: " + st.profile_text()))
            row = QHBoxLayout()
            ok = QPushButton("Принять профиль")
            ok.setObjectName("Primary")
            no = QPushButton("Оставить как есть")
            ok.clicked.connect(lambda: (st.resolve_proposal(True), self.rebuild()))
            no.clicked.connect(lambda: (st.resolve_proposal(False), self.rebuild()))
            row.addWidget(ok)
            row.addWidget(no)
            row.addStretch(1)
            card.add_layout(row)
        changes = [dict(c, source="weekly") for c in weekly.get("topic_changes") or []] + \
                  [dict(t, source="takeout") for t in topics]
        for ch in changes[:6]:
            row = QHBoxLayout()
            w = float(ch.get("weight", 1) or 0)
            text = (f"Тема «{ch['name']}»: " + ("убрать" if w <= 0 else f"вес {w:.1f}")
                    + (f" — {ch.get('reason') or ch.get('description', '')}" if ch.get("reason") or
                       ch.get("description") else ""))
            lab = QLabel(text)
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            apply_btn = QPushButton("Применить")
            apply_btn.setCursor(Qt.PointingHandCursor)
            apply_btn.clicked.connect(lambda _=False, ch=ch: self._apply_topic(ch))
            row.addWidget(apply_btn)
            card.add_layout(row)
        self.body.addWidget(card)

    def _apply_topic(self, ch: dict) -> None:
        st = self.c.storage
        w = float(ch.get("weight", 1) or 0)
        if w <= 0:
            st.delete_topic(ch["name"])
        else:
            existing = {t["name"]: t for t in st.topics(enabled_only=False)}
            t = existing.get(ch["name"])
            st.upsert_topic(ch["name"], (t or {}).get("description") or ch.get("description", ""), min(1.5, w))
        key = "topic_suggestions" if ch.get("source") == "takeout" else "weekly_suggestions"
        if key == "topic_suggestions":
            left = [t for t in jload(st.meta_get(key, ""), []) if t.get("name") != ch["name"]]
            st.meta_set(key, json.dumps(left, ensure_ascii=False))
        else:
            data = jload(st.meta_get(key, ""), {})
            data["topic_changes"] = [t for t in data.get("topic_changes") or [] if t.get("name") != ch["name"]]
            st.meta_set(key, json.dumps(data, ensure_ascii=False))
        self.rebuild()

    def _muted(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Muted")
        lab.setWordWrap(True)
        return lab

    def _learning(self) -> QWidget:
        sec = W.Section("Обучение ленты", "Насколько лента вас понимает, импорт из Google, ручные правки")
        q = quality(self.c.storage)
        tiles = QWidget()
        tl = QHBoxLayout(tiles)
        tl.setContentsMargins(0, 8, 0, 8)

        def pct(v):
            return "—" if v is None else f"{round(v * 100)}%"
        for value, label in ((pct(q["precision_top5"]), "ценного в «Главном»"),
                             (pct(q["reject_rate"]), "отвергнуто"),
                             (pct(q["valuable_time"]), "времени на ценное"),
                             (pct(q["explore_success"]), "удачной разведки")):
            tile = W.StatTile(label)
            tile.set(value)
            tl.addWidget(tile)
        sec.add_widget(tiles)
        ranker = self.c.ranker()
        w = ranker.weights
        sec.add_row("Чему лента доверяет", (f"ваш вкус {round(w['personal'] * 100)}% · профиль "
                                             f"{round(w['fit'] * 100)}% · важность {round(w['importance'] * 100)}% · "
                                             f"свежесть {round(w['fresh'] * 100)}% — подстраивается сама"), None)
        sec.add_row("Сколько о вас знаем", f"{round(ranker.model.confidence * 100)}% "
                    f"({self.c.storage.interactions_count()} действий)", None)
        sec.add_row("Персонализация", "Выключите, чтобы сравнить с лентой без подстройки",
                    W.bind_switch(self.c.settings, "learning.personalization", lambda _v: self.c.refresh_feed()))
        imp = QPushButton("Импорт из Google…")
        imp.setCursor(Qt.PointingHandCursor)
        imp.clicked.connect(self._takeout)
        sec.add_row("Интересы из YouTube и поиска Google",
                    "takeout.google.com → «YouTube и YouTube Music» и «Мои действия». Claude получит только "
                    "названия видео, каналов и запросов за год", imp)
        wk = QPushButton("Разобрать неделю")
        wk.setCursor(Qt.PointingHandCursor)
        wk.clicked.connect(lambda: self.c.weekly_now(lambda _r: self.rebuild()))
        sec.add_row("Еженедельный разбор", "Claude смотрит, что вы читали, и предлагает правки профиля", wk)
        return sec

    def _takeout(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.area, "Выгрузка Google Takeout", "",
                                              "Архив или файл (*.zip *.json *.html *.csv);;Все файлы (*)")
        if not path:
            return
        self.c.import_takeout(path, lambda _r: self.rebuild())

    def _translation(self) -> QWidget:
        sec = W.Section("Перевод", "Сколько переводить заранее, модель, ваши термины")
        sec.add_row("Переводить заранее", "Сразу после сбора — лучшие по прогнозу статьи",
                    W.bind_spin(self.c.settings, "translate.prefetch_top", 0, 30, " статей"))
        sec.add_row("Модель перевода", "Sonnet — баланс качества и лимитов", W.bind_combo(
            self.c.settings, "translate.model", [("sonnet", "Sonnet"), ("haiku", "Haiku (экономнее)"),
                                                 ("opus", "Opus (лучше, тратит больше)")]))
        st = self.c.storage
        for orig, ru in st.glossary().items():
            rm = QPushButton("✕")
            rm.setObjectName("Icon")
            rm.clicked.connect(lambda _=False, o=orig: (st.execute("DELETE FROM glossary WHERE term_orig=?", (o,)),
                                                        self.rebuild()))
            sec.add_row(f"{orig} → {ru}", "", rm)
        add = QWidget()
        al = QHBoxLayout(add)
        al.setContentsMargins(0, 10, 0, 10)
        a = QLineEdit()
        a.setPlaceholderText("Термин в оригинале")
        b = QLineEdit()
        b.setPlaceholderText("Как переводить")
        btn = QPushButton("Добавить термин")
        btn.clicked.connect(lambda: (st.set_glossary(a.text(), b.text()), self.rebuild())
                            if a.text().strip() and b.text().strip() else None)
        al.addWidget(a)
        al.addWidget(b)
        al.addWidget(btn)
        sec.add_widget(add)
        return sec

    def _storage(self) -> QWidget:
        sec = W.Section("Хранение и скрытое", "Сколько хранить статьи, что вы скрыли")
        sec.add_row("Хранить статьи", "Сохранённое хранится всегда; то, что лента о вас узнала, не теряется",
                    W.bind_spin(self.c.settings, "storage.keep_days", 3, 3650, " дн."))
        names = {"mute_source": "Источник", "mute_topic": "Тема", "mute_entity": "Про"}
        for r in self.c.storage.active_rules():
            back = QPushButton("Вернуть")
            back.clicked.connect(lambda _=False, rid=r["id"]: (self.c.storage.remove_rule(rid), self.rebuild(),
                                                               self.c.refresh_feed()))
            sec.add_row(f"{names.get(r['kind'], r['kind'])}: {r['target']}", "скрыто", back)
        return sec

    def _journal(self) -> QWidget:
        sec = W.Section("Журнал запусков Claude", "Сборы, поиски и их стоимость в лимитах подписки")
        kinds = {"collect": "Сбор", "search": "Поиск"}
        statuses = {"ok": "готово", "empty": "нового нет", "partial": "частично", "failed": "не удалось",
                    "running": "идёт"}
        for r in self.c.storage.runs(10):
            when = time.strftime("%d.%m %H:%M", time.localtime(r["started"]))
            text = f"{when} · {kinds.get(r['kind'], r['kind'])}" + (f" «{r['query']}»" if r["query"] else "")
            hint = f"{statuses.get(r['status'], r['status'])}, статей {r['saved']}, ≈${r['cost_usd']:.2f}"
            if r["error"]:
                hint += " — " + r["error"][:120]
            sec.add_row(text, hint, None)
        return sec
