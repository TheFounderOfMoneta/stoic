"""Читалка: статья целиком по-русски, оригинал в один клик, режим сосредоточенности.

- Колонка 680 px (~66 знаков в строке), шрифт Literata, мягкий цвет текста.
- Сверху «Коротко»; перевод появляется абзац за абзацем, пока Claude переводит.
- У абзаца при наведении — «EN»: оригинал раскрывается под ним.
- Время чтения (только пока окно активно и вы что-то делаете) и глубина прокрутки идут в обучение.
- В конце: реакции, иногда опрос «Стоило времени?», вопрос Claude по статье, «Похожее».
"""
from __future__ import annotations

import hashlib
import html
import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..config import IMAGES_DIR
from ..rank.features import KINDS
from ..rank.signals import expected_ms
from ..util import words_count
from . import widgets as W
from .feed_view import age_text
from .look import LINE_HEIGHT, READER_W

ACTIVE_GAP_S = 60      # без мыши/клавиатуры дольше — время не считается


def _html(text: str) -> str:
    return f'<div style="line-height:{LINE_HEIGHT}%;">{html.escape(text or "").replace(chr(10), "<br>")}</div>'


class _ImageLoader(QObject):
    loaded = Signal(str, str)      # url, путь к файлу

    def fetch(self, url: str) -> None:
        def work():
            import httpx
            name = hashlib.sha1(url.encode()).hexdigest()[:20]
            path = IMAGES_DIR / name
            if not path.exists():
                try:
                    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
                    if r.status_code != 200 or len(r.content) > 8_000_000:
                        return
                    path.write_bytes(r.content)
                except Exception:  # noqa: BLE001
                    return
            self.loaded.emit(url, str(path))
        threading.Thread(target=work, daemon=True).start()


class BlockWidget(QFrame):
    """Один блок статьи: перевод (или оригинал), а при наведении — «EN» для оригинала."""

    def __init__(self, block: dict, mode: str, translating: bool):
        super().__init__()
        self.block = block
        self.mode = mode
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setTextFormat(Qt.RichText)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.orig = QLabel()
        self.orig.setObjectName("BodyOrig")
        self.orig.setWordWrap(True)
        self.orig.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.orig.hide()
        col.addWidget(self.label)
        col.addWidget(self.orig)
        lay.addLayout(col, 1)
        self.en = QPushButton("EN")
        self.en.setObjectName("Original")
        self.en.setToolTip("Показать оригинал абзаца")
        self.en.setCursor(Qt.PointingHandCursor)
        self.en.setFixedWidth(30)
        self.en.clicked.connect(self.toggle_original)
        self.en.setVisible(False)
        lay.addWidget(self.en, 0, Qt.AlignTop)
        if block["type"] == "quote":
            self.setObjectName("Quote")
            lay.setContentsMargins(16, 0, 0, 0)
        self.render(translating)

    def text(self) -> str:
        b = self.block
        if self.mode == "orig" or b["type"] in ("code", "img"):
            return b["text_orig"]
        return b["text_ru"] or b["text_orig"]

    def render(self, translating: bool = False) -> None:
        b = self.block
        t = b["type"]
        has_ru = bool(b.get("text_ru"))
        faded = self.mode == "ru" and not has_ru and translating
        name = {"h2": "ReaderH2", "h3": "ReaderH3", "quote": "QuoteText", "code": "Code"}.get(
            t, "BodyFaded" if faded else "Body")
        self.label.setObjectName(name)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        text = self.text()
        if t == "li":
            text = "•  " + text
        if t == "code":
            self.label.setTextFormat(Qt.PlainText)
            self.label.setText(text)
        elif t in ("h2", "h3"):
            self.label.setTextFormat(Qt.PlainText)
            self.label.setText(text)
        else:
            self.label.setTextFormat(Qt.RichText)
            self.label.setText(_html(text))

    def set_translation(self, ru: str) -> None:
        self.block["text_ru"] = ru
        self.render(False)

    def toggle_original(self) -> None:
        if self.orig.isVisible():
            self.orig.hide()
        else:
            self.orig.setText(self.block["text_orig"] if self.mode == "ru" else (self.block.get("text_ru") or ""))
            self.orig.show()

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.block["type"] not in ("code", "img", "h2", "h3") and self.block.get("text_ru") \
                and self.block["text_ru"] != self.block["text_orig"]:
            self.en.setText("EN" if self.mode == "ru" else "RU")
            self.en.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.en.setVisible(False)
        super().leaveEvent(event)


class ReaderView(QWidget):
    back = Signal()
    open_other = Signal(int)

    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.article: dict | None = None
        self.blocks: list[dict] = []
        self.block_widgets: dict[int, BlockWidget] = {}
        self.mode = "ru"
        self.active_ms = 0
        self.max_scroll = 0.0
        self.opened_at = 0.0
        self.translating = False
        self.images = _ImageLoader()
        self.images.loaded.connect(self._image_loaded)
        self.image_labels: dict[str, QLabel] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._top_bar())
        self.progress = QProgressBar()
        self.progress.setObjectName("ReadProgress")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.setRange(0, 1000)
        outer.addWidget(self.progress)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.NoFrame)
        self.area.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.area.verticalScrollBar().rangeChanged.connect(lambda *_: self._scrolled())
        outer.addWidget(self.area, 1)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.tick.start(1000)
        for keys, fn in (("Escape", self.back.emit), ("O", self.toggle_mode), ("S", self.toggle_save),
                         ("L", lambda: self._key_react("like")), ("D", lambda: self._key_react("dislike")),
                         ("F", lambda: self._key_react("follow"))):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ------------------------------------------------------------- каркас
    def _top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 20, 10)
        back = QPushButton("‹  Лента")
        back.setObjectName("Link")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.back.emit)
        self.back_btn = back
        self.top_meta = QLabel("")
        self.top_meta.setObjectName("Meta")
        self.seg = W.Segmented(["Перевод", "Оригинал"])
        self.seg.changed.connect(lambda i: self.set_mode("orig" if i == 1 else "ru"))
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setCheckable(True)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.toggle_save)
        more = QPushButton("⋯")
        more.setObjectName("Icon")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self._more_menu(more))
        lay.addWidget(back)
        lay.addStretch(1)
        lay.addWidget(self.top_meta)
        lay.addStretch(1)
        lay.addWidget(self.seg)
        lay.addSpacing(8)
        lay.addWidget(self.save_btn)
        lay.addWidget(more)
        return bar

    # ------------------------------------------------------------- открыть статью
    def open(self, article_id: int) -> None:
        self.close_session()
        a = self.c.storage.article(article_id)
        if not a:
            return
        self.article = a
        self.blocks = self.c.storage.blocks(article_id)
        self.mode = "ru"
        self.active_ms = 0
        self.max_scroll = 0.0
        self.opened_at = time.time()
        self.c.storage.log_event(article_id, "open")
        self.seg.buttons[0].setChecked(True)
        self.seg.setVisible(a.get("lang") != "ru" and bool(self.blocks))
        self.save_btn.setChecked(bool(a.get("saved")))
        self.save_btn.setText("Сохранено" if a.get("saved") else "Сохранить")
        self._build()
        self.area.verticalScrollBar().setValue(0)
        QTimer.singleShot(50, self._restore_position)
        need = a.get("lang") != "ru" and a.get("extract_status") == "ok" and a.get("translate_status") != "done" \
            and any(b["type"] in ("p", "h2", "h3", "li", "quote", "table") and not b["text_ru"] for b in self.blocks)
        if need:
            self.translating = True
            self.translate_label.setText("Переводится…")
            self.translate_label.show()
            self.c.translate(article_id, self._on_block, self._on_translated, self._on_progress)
        else:
            self.translating = False

    def _restore_position(self) -> None:
        pos = float((self.article or {}).get("read_pos") or 0)
        sb = self.area.verticalScrollBar()
        if 0.05 < pos < 0.95 and sb.maximum() > 0:
            sb.setValue(int(pos * sb.maximum()))

    def _build(self) -> None:
        a = self.article
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(36, 34, 36, 60)
        column = QWidget()
        column.setFixedWidth(READER_W)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        row.addStretch(1)
        row.addWidget(column)
        row.addStretch(1)
        words = int(a.get("words") or 0)
        meta = [a.get("topic", ""), a.get("source") or a.get("domain", ""),
                age_text(a.get("published_at") or a.get("collected_at")),
                f"{max(1, round(expected_ms(words) / 60000))} мин чтения" if words else "",
                KINDS.get(a.get("kind", ""), "")]
        meta_label = QLabel("  ·  ".join(m for m in meta if m).upper())
        meta_label.setObjectName("ReaderMeta")
        meta_label.setWordWrap(True)
        col.addWidget(meta_label)
        title = QLabel(a.get("title_ru") or a.get("title_orig") or "")
        title.setObjectName("ReaderTitle")
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(title)
        if a.get("title_orig") and a.get("lang") != "ru":
            orig = QLabel(a["title_orig"])
            orig.setObjectName("Caption")
            orig.setWordWrap(True)
            col.addWidget(orig)
        bullets = a.get("summary_ru") or []
        if bullets:
            short = W.Card("", "", padding=16)
            head = QHBoxLayout()
            lab = QLabel("КОРОТКО")
            lab.setObjectName("SectionLabel")
            head.addWidget(lab)
            head.addStretch(1)
            toggle = QPushButton("Свернуть" if not self.c.settings.get("ui.short_collapsed") else "Показать")
            toggle.setObjectName("Link")
            toggle.setCursor(Qt.PointingHandCursor)
            head.addWidget(toggle)
            short.add_layout(head)
            items = []
            for b in bullets:
                bl = QLabel("•  " + b)
                bl.setObjectName("Soft")
                bl.setWordWrap(True)
                short.add(bl)
                items.append(bl)
            if a.get("why_ru"):
                why = QLabel("Почему вам: " + a["why_ru"])
                why.setObjectName("Muted")
                why.setWordWrap(True)
                short.add(why)
                items.append(why)

            def flip():
                hidden = not self.c.settings.get("ui.short_collapsed")
                self.c.settings.set("ui.short_collapsed", hidden)
                for w in items:
                    w.setVisible(not hidden)
                toggle.setText("Показать" if hidden else "Свернуть")
            toggle.clicked.connect(flip)
            for w in items:
                w.setVisible(not self.c.settings.get("ui.short_collapsed"))
            col.addWidget(short)
        if not self.blocks or a.get("extract_status") in ("paywall", "captcha", "failed", "short"):
            note = {"paywall": "Полный текст доступен только по подписке на сайте.",
                    "captcha": "Сайт закрыт проверкой на робота — полный текст не получен.",
                    "short": "Текста статьи почти нет — возможно, это видео или галерея."}.get(
                a.get("extract_status"), "Полный текст не удалось получить.")
            banner = QLabel(note + " Выше — главное из статьи.")
            banner.setObjectName("Banner")
            banner.setWordWrap(True)
            col.addWidget(banner)
            site = QPushButton("Открыть на сайте")
            site.setObjectName("Primary")
            site.setCursor(Qt.PointingHandCursor)
            site.clicked.connect(lambda: self.c.open_url(a["url"]))
            col.addWidget(site, 0, Qt.AlignLeft)
        self.block_widgets.clear()
        self.image_labels.clear()
        translating_soon = a.get("lang") != "ru" and a.get("translate_status") != "done"
        for b in self.blocks:
            if b["type"] == "img":
                if not b.get("src"):
                    continue
                img = QLabel()
                img.setAlignment(Qt.AlignCenter)
                img.setMinimumHeight(40)
                col.addWidget(img)
                if b.get("text_orig"):
                    cap = QLabel(b.get("text_ru") or b["text_orig"])
                    cap.setObjectName("Caption")
                    cap.setWordWrap(True)
                    col.addWidget(cap)
                self.image_labels[b["src"]] = img
                self.images.fetch(b["src"])
                continue
            w = BlockWidget(b, self.mode, translating_soon)
            self.block_widgets[b["idx"]] = w
            col.addWidget(w)
        self.translate_label = QLabel("")
        self.translate_label.setObjectName("Hint")
        self.translate_label.hide()
        col.addWidget(self.translate_label)
        col.addSpacing(10)
        col.addWidget(W.divider())
        col.addLayout(self._reactions())
        if self.c.should_survey(a["id"]):
            col.addWidget(self._survey())
        col.addLayout(self._ask())
        self.answer = QLabel("")
        self.answer.setObjectName("Answer")
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.answer.hide()
        col.addWidget(self.answer)
        similar = self.c.similar(a)
        if similar:
            from .widgets import Group
            g = Group("Похожее")
            for it in similar:
                b = QPushButton(it.article.get("title_ru") or it.article.get("title_orig") or "")
                b.setObjectName("Link")
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet("text-align: left; padding: 8px 0;")
                b.clicked.connect(lambda _=False, i=it.id: self.open_other.emit(i))
                g.add_widget(b)
            col.addWidget(g)
        col.addStretch(1)
        self.area.setWidget(body)
        self._update_meta()

    def _reactions(self) -> QHBoxLayout:
        a = self.article
        state = self.c.reaction_state(a["id"])
        lay = QHBoxLayout()
        lay.setSpacing(6)
        self.pills = {}
        for key, text in (("superlike", "🔥 Круто"), ("like", "👍 Интересно"), ("dislike", "👎 Не моя тема"),
                          ("follow", "👀 Следить")):
            b = QPushButton(text)
            b.setObjectName("Pill")
            b.setCheckable(True)
            b.setChecked(bool(state.get(key)))
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(lambda on, k=key: self._react(k, on))
            lay.addWidget(b)
            self.pills[key] = b
        more = QPushButton("⋯")
        more.setObjectName("Pill")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self._reaction_menu(more))
        lay.addWidget(more)
        lay.addStretch(1)
        return lay

    def _reaction_menu(self, anchor) -> None:
        state = self.c.reaction_state(self.article["id"])
        menu = QMenu(self)
        for key, text in (("known", "🥱 Уже знал"), ("shallow", "Поверхностно"), ("clickbait", "💩 Кликбейт")):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(bool(state.get(key)))
            act.toggled.connect(lambda on, k=key: self._react(k, on))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _react(self, key: str, on: bool) -> None:
        if not self.article:
            return
        # взаимоисключающие: «Интересно»/«Круто» против «Не моя тема»
        opposite = {"like": ["dislike"], "superlike": ["dislike"], "dislike": ["like", "superlike"]}
        if on:
            for o in opposite.get(key, []):
                if o in self.pills and self.pills[o].isChecked():
                    self.pills[o].blockSignals(True)
                    self.pills[o].setChecked(False)
                    self.pills[o].blockSignals(False)
                    self.c.react(self.article["id"], o, False)
        self.c.react(self.article["id"], key, on)

    def _key_react(self, key: str) -> None:
        if key in getattr(self, "pills", {}):
            self.pills[key].toggle()

    def _survey(self) -> QWidget:
        card = W.Card("", "", padding=16)
        row = QHBoxLayout()
        q = QLabel("Стоило потраченного времени?")
        q.setObjectName("CardTitle")
        row.addWidget(q)
        row.addStretch(1)
        stars = []
        for i in range(1, 6):
            s = QPushButton("★")
            s.setObjectName("Star")
            s.setCheckable(True)
            s.setCursor(Qt.PointingHandCursor)
            stars.append(s)
            row.addWidget(s)
        note = QLabel("Короткий вопрос появляется не всегда — так лента учится отличать «прочитал» от «было полезно».")
        note.setObjectName("Muted")
        note.setWordWrap(True)

        def choose(n: int):
            for k, s in enumerate(stars, start=1):
                s.setChecked(k <= n)
            self.c.survey(self.article["id"], n)
            note.setText("Спасибо — учту." if n >= 3 else "Спасибо — буду реже предлагать похожее.")
        for k, s in enumerate(stars, start=1):
            s.clicked.connect(lambda _=False, n=k: choose(n))
        card.add_layout(row)
        card.add(note)
        return card

    def _ask(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(8)
        self.ask_line = QLineEdit()
        self.ask_line.setPlaceholderText("Спросить Claude об этой статье…")
        go = QPushButton("Спросить")
        go.setObjectName("Primary")
        go.setCursor(Qt.PointingHandCursor)

        def submit():
            q = self.ask_line.text().strip()
            if not q or not self.article:
                return
            self.answer.setText("Claude думает…")
            self.answer.show()
            self.c.ask(self.article["id"], q, self._answered)
        go.clicked.connect(submit)
        self.ask_line.returnPressed.connect(submit)
        lay.addWidget(self.ask_line, 1)
        lay.addWidget(go)
        return lay

    def _answered(self, text: str) -> None:
        self.answer.setText(text)
        self.answer.show()

    def _more_menu(self, anchor) -> None:
        a = self.article
        if not a:
            return
        menu = QMenu(self)
        menu.addAction("Открыть на сайте", lambda: self.c.open_url(a["url"]))
        menu.addAction("Почему эта статья в ленте", lambda: self.c.explain(a["id"], self))
        menu.addSeparator()
        if a.get("domain"):
            menu.addAction(f"Не показывать {a['domain']}", lambda: self._mute("mute_source", a["domain"]))
        if a.get("topic"):
            menu.addAction(f"Не показывать тему «{a['topic']}»", lambda: self._mute("mute_topic", a["topic"]))
        for e in (a.get("entities") or [])[:2]:
            menu.addAction(f"Не показывать про «{e}»", lambda e=e: self._mute("mute_entity", e))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _mute(self, kind: str, target: str) -> None:
        self.c.add_rule(kind, target)
        self.back.emit()

    # ------------------------------------------------------------- перевод
    def _on_block(self, idx: int, text: str) -> None:
        w = self.block_widgets.get(idx)
        if w is not None and self.article:
            w.set_translation(text)
        self._update_meta()

    def _on_progress(self, msg: str) -> None:
        self.translate_label.setText(msg)

    def _on_translated(self, res: dict) -> None:
        self.translating = False
        if self.article:                     # подтянуть всё, что записал перевод (и то, что не переводится)
            fresh = {b["idx"]: b["text_ru"] for b in self.c.storage.blocks(self.article["id"])}
            for idx, w in self.block_widgets.items():
                if fresh.get(idx):
                    w.block["text_ru"] = fresh[idx]
            self.article["translate_status"] = res.get("status", self.article.get("translate_status"))
        if res.get("status") == "done":
            self.translate_label.hide()
        else:
            self.translate_label.setText(res.get("message", ""))
            self.translate_label.show()
        for w in self.block_widgets.values():
            w.render(False)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        for w in self.block_widgets.values():
            w.mode = mode
            w.render(self.translating)

    def toggle_mode(self) -> None:
        if self.seg.isVisible():
            i = 0 if self.mode == "orig" else 1
            self.seg.buttons[i].setChecked(True)
            self.set_mode("orig" if i == 1 else "ru")

    def toggle_save(self) -> None:
        if not self.article:
            return
        on = not bool(self.article.get("saved"))
        self.article["saved"] = int(on)
        self.save_btn.setChecked(on)
        self.save_btn.setText("Сохранено" if on else "Сохранить")
        self.c.set_saved(self.article["id"], on)

    def _image_loaded(self, url: str, path: str) -> None:
        lab = self.image_labels.get(url)
        if lab is None:
            return
        pm = QPixmap(path)
        if pm.isNull():
            lab.hide()
            return
        if pm.width() > READER_W:
            pm = pm.scaledToWidth(READER_W, Qt.SmoothTransformation)
        lab.setPixmap(pm)

    # ------------------------------------------------------------- время чтения
    def _scrolled(self, *_):
        sb = self.area.verticalScrollBar()
        page = max(1, sb.pageStep())
        pct = (sb.value() + page) / (sb.maximum() + page) if sb.maximum() > 0 else 1.0
        self.max_scroll = max(self.max_scroll, pct)
        self.progress.setValue(int(pct * 1000))
        self._update_meta()

    def _update_meta(self) -> None:
        a = self.article
        if not a:
            return
        words = int(a.get("words") or 0)
        left = ""
        if words:
            sb = self.area.verticalScrollBar()
            pos = sb.value() / sb.maximum() if sb.maximum() > 0 else 0
            mins = max(0, round(expected_ms(int(words * (1 - pos))) / 60000))
            left = "дочитано" if pos >= 0.97 else f"осталось {max(1, mins)} мин"
        self.top_meta.setText("  ·  ".join(x for x in (a.get("source") or a.get("domain", ""), left) if x))

    def _tick(self) -> None:
        if not self.article or not self.isVisible():
            return
        win = self.window()
        if win is None or not win.isActiveWindow():
            return
        if time.time() - self.c.last_activity < ACTIVE_GAP_S:
            self.active_ms += 1000

    def close_session(self) -> None:
        """Уходя со статьи — записать, сколько и как читали."""
        a = self.article
        if not a:
            return
        words = int(a.get("words") or 0) or words_count(" ".join(b["text_orig"] for b in self.blocks))
        exp = expected_ms(words)
        sb = self.area.verticalScrollBar()
        pos = sb.value() / sb.maximum() if sb.maximum() > 0 else 0.0
        self.c.finish_read(a["id"], self.active_ms, self.max_scroll, exp, self.mode, pos)
        self.article = None
