"""Читалка: статья целиком и только по-русски, режим сосредоточенности.

- Колонка 680 px (~66 знаков в строке), шрифт Literata, мягкий цвет текста.
- Сверху «Коротко»; русский текст — редакторский перевод Claude, абзацы появляются по мере готовности.
  Оригинала на экране нет (он не нужен); если что — «Открыть на сайте» в меню «⋯».
- Время чтения (только пока окно активно и вы что-то делаете) и глубина прокрутки идут в обучение.
- В конце: реакции, иногда опрос «Стоило времени?», вопрос Claude по статье, «Похожее».
"""
from __future__ import annotations

import hashlib
import html
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..config import IMAGES_DIR
from ..rank.features import KINDS
from ..rank.signals import expected_ms
from ..translate import TEXT_TYPES, is_condensed
from ..util import words_count
from . import widgets as W
from .feed_view import age_text
from .look import LINE_HEIGHT, READER_W

ACTIVE_GAP_S = 60      # без мыши/клавиатуры дольше — время не считается


def _html(text: str) -> str:
    return f'<div style="line-height:{LINE_HEIGHT}%;">{html.escape(text or "").replace(chr(10), "<br>")}</div>'


def _table_html(text: str) -> str:
    rows = [r.split(" | ") for r in (text or "").split("\n") if r.strip()]
    cells = "".join("<tr>" + "".join(f'<td style="padding:4px 10px 4px 0;">{html.escape(c)}</td>' for c in r)
                    + "</tr>" for r in rows)
    return f'<table cellspacing="0">{cells}</table>'


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


def block_label(block: dict) -> QLabel:
    """Один блок текста: абзац, подзаголовок, пункт, цитата, код или таблица."""
    t, text = block["type"], block.get("text") or ""
    lab = QLabel()
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    lab.setObjectName({"h2": "ReaderH2", "h3": "ReaderH3", "quote": "QuoteText", "code": "Code"}.get(t, "Body"))
    if t in ("h2", "h3", "code"):
        lab.setTextFormat(Qt.PlainText)
        lab.setText(text)
    elif t == "table":
        lab.setTextFormat(Qt.RichText)
        lab.setText(_table_html(text))
    else:
        lab.setTextFormat(Qt.RichText)
        lab.setText(_html(("•  " + text) if t == "li" else text))
    if t == "quote":
        wrap = QFrame()
        wrap.setObjectName("Quote")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(16, 0, 0, 0)
        lay.addWidget(lab)
        wrap.label = lab
        return wrap
    return lab


class ReaderView(QWidget):
    back = Signal()
    open_other = Signal(int)

    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.article: dict | None = None
        self.shown: list[dict] = []          # блоки на экране
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
        for keys, fn in (("Escape", self.back.emit), ("S", self.toggle_save),
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
        lay.addWidget(self.save_btn)
        lay.addWidget(more)
        return bar

    # ------------------------------------------------------------- открыть статью
    def _russian_blocks(self, a: dict) -> tuple[list[dict], bool]:
        """(блоки для показа, нужен ли перевод)."""
        st = self.c.storage
        if a.get("lang") == "ru":
            return [dict(b, text=b["text_orig"]) for b in st.blocks(a["id"])], False
        ru = st.ru_blocks(a["id"])
        if ru and a.get("translate_status") == "done":
            return ru, False
        legacy = st.blocks(a["id"])            # статьи, переведённые старым способом (абзац в абзац)
        text = [b for b in legacy if b["type"] in TEXT_TYPES]
        if text and all(b["text_ru"] for b in text):
            return [dict(b, text=b["text_ru"] if b["type"] in TEXT_TYPES else b["text_orig"]) for b in legacy], False
        can = a.get("extract_status") == "ok" and bool(text)
        return (ru if can else []), can

    def open(self, article_id: int) -> None:
        self.close_session()
        a = self.c.storage.article(article_id)
        if not a:
            return
        self.article = a
        self.active_ms = 0
        self.max_scroll = 0.0
        self.opened_at = time.time()
        self.c.storage.log_event(article_id, "open")
        self.save_btn.setChecked(bool(a.get("saved")))
        self.save_btn.setText("Сохранено" if a.get("saved") else "Сохранить")
        blocks, need = self._russian_blocks(a)
        self.translating = need
        self._build([] if need else blocks)
        self.area.verticalScrollBar().setValue(0)
        QTimer.singleShot(50, self._restore_position)
        if need:
            self.status.setText("Claude переводит статью — абзацы появятся здесь через несколько секунд…")
            self.status.show()
            self.c.translate(article_id, self._on_block, self._on_translated, self._on_progress)

    def _restore_position(self) -> None:
        pos = float((self.article or {}).get("read_pos") or 0)
        sb = self.area.verticalScrollBar()
        if 0.05 < pos < 0.95 and sb.maximum() > 0:
            sb.setValue(int(pos * sb.maximum()))

    def _build(self, blocks: list[dict]) -> None:
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
        bullets = a.get("summary_ru") or []
        if bullets:
            col.addWidget(self._short(bullets, a.get("why_ru", "")))
        if a.get("extract_status") in ("paywall", "captcha", "failed", "short") or \
                (not blocks and not self.translating):
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
        elif a.get("lang") != "ru" and is_condensed(a):
            hint = QLabel("Длинный материал — здесь сжатый пересказ со всеми главными фактами. "
                          "Полностью — «Открыть на сайте» в меню ⋯.")
            hint.setObjectName("Hint")
            hint.setWordWrap(True)
            col.addWidget(hint)
        # текст статьи: свой контейнер, чтобы абзацы можно было дописывать по мере перевода
        self.text_box = QWidget()
        self.text_lay = QVBoxLayout(self.text_box)
        self.text_lay.setContentsMargins(0, 0, 0, 0)
        self.text_lay.setSpacing(16)
        col.addWidget(self.text_box)
        self.shown = []
        self.image_labels.clear()
        for b in blocks:
            self._add_block(b)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        self.status.hide()
        col.addWidget(self.status)
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
            g = W.Group("Похожее")
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

    def _short(self, bullets: list[str], why: str) -> QWidget:
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
        if why:
            w = QLabel("Почему вам: " + why)
            w.setObjectName("Muted")
            w.setWordWrap(True)
            short.add(w)
            items.append(w)

        def flip():
            hidden = not self.c.settings.get("ui.short_collapsed")
            self.c.settings.set("ui.short_collapsed", hidden)
            for x in items:
                x.setVisible(not hidden)
            toggle.setText("Показать" if hidden else "Свернуть")
        toggle.clicked.connect(flip)
        for x in items:
            x.setVisible(not self.c.settings.get("ui.short_collapsed"))
        return short

    def _clear_body(self) -> None:
        while self.text_lay.count():
            w = self.text_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.shown = []
        self.image_labels.clear()

    def _add_block(self, b: dict) -> None:
        if b["type"] == "img":
            if not b.get("src"):
                return
            img = QLabel()
            img.setAlignment(Qt.AlignCenter)
            img.setMinimumHeight(40)
            self.text_lay.addWidget(img)
            self.image_labels[b["src"]] = img
            self.images.fetch(b["src"])
        elif (b.get("text") or "").strip():
            self.text_lay.addWidget(block_label(b))
        else:
            return
        self.shown.append(b)

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
        for _i in range(1, 6):
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
    def _on_block(self, block: dict) -> None:
        if not self.article:
            return
        self.status.setText("Переводится…")
        self._add_block(block)
        self._update_meta()

    def _on_progress(self, msg: str) -> None:
        self.status.setText(msg)

    def _on_translated(self, res: dict) -> None:
        self.translating = False
        if not self.article:
            return
        self.article["translate_status"] = {"done": "done", "partial": "partial"}.get(res.get("status"), "none")
        if res.get("status") == "done":
            self.status.hide()
            # в потоке абзацы могли прийти не все — показываем то, что сохранено
            saved = self.c.storage.ru_blocks(self.article["id"])
            if len(saved) != len(self.shown):
                self._clear_body()
                for b in saved:
                    self._add_block(b)
        else:
            self.status.setText(res.get("message", ""))
            self.status.show()

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
        words = self._words()
        left = ""
        if words:
            sb = self.area.verticalScrollBar()
            pos = sb.value() / sb.maximum() if sb.maximum() > 0 else 0
            mins = max(0, round(expected_ms(int(words * (1 - pos))) / 60000))
            left = "дочитано" if pos >= 0.97 else f"осталось {max(1, mins)} мин"
        self.top_meta.setText("  ·  ".join(x for x in (a.get("source") or a.get("domain", ""), left) if x))

    def _words(self) -> int:
        """Слов в том, что человек читает (пересказ короче оригинала)."""
        n = sum(words_count(b.get("text") or "") for b in self.shown if b["type"] in TEXT_TYPES)
        return n or int((self.article or {}).get("words") or 0)

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
        exp = expected_ms(self._words())
        sb = self.area.verticalScrollBar()
        pos = sb.value() / sb.maximum() if sb.maximum() > 0 else 0.0
        self.c.finish_read(a["id"], self.active_ms, self.max_scroll, exp, "ru", pos)
        self.article = None
