# Как запустить: рядом положить пакет mock/ с копиями aqualinux/ui/theme.py и widgets.py
# (mock/ui/theme.py, mock/ui/widgets.py; mock/config.py с CACHE_DIR; mock/hotkeys.py с pretty_token)
# и fonts/Literata.ttf, затем: python mockup.py <папка для PNG>
"""Статичный макет «Сводки» в стиле Aqua Linux: Лента, Читалка (начало и конец), Поиск.

Только внешний вид: данные выдуманы, логики нет. Рендер без экрана в PNG (2x).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPen,  # noqa: E402
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar,  # noqa: E402
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from mock.ui import theme  # noqa: E402
from mock.ui import widgets as W  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication([])
app.setStyle("Fusion")
for f in ("Literata.ttf", "Literata-Italic.ttf"):
    fid = QFontDatabase.addApplicationFont(os.path.join(HERE, "fonts", f))
    print("font", f, QFontDatabase.applicationFontFamilies(fid))
theme.apply_app_font(app)

C = theme.DARK
W.PALETTE.clear()
W.PALETTE.update(C)

SIDEBAR_W = 216
CONTENT_MAX_W = 760
READER_W = 680
READ_FONT = "Literata"
READ_COLOR = "#D5DCE7"   # чуть мягче основного текста — для долгого чтения в тёмной теме

EXTRA = f"""
#Row {{ border-radius: 10px; }}
#RowHover {{ background: {C['hover']}; border-radius: 10px; }}
#FeedTitle {{ font-size: 12.4pt; font-weight: 650; letter-spacing: -0.15px; }}
#FeedTitleRead {{ font-size: 12.4pt; font-weight: 600; letter-spacing: -0.15px; color: {C['muted']}; }}
#FeedSummary {{ color: {C['text_soft']}; font-size: 10.1pt; }}
#Meta {{ color: {C['muted']}; font-size: 8.8pt; }}
#MetaTopic {{ color: {C['accent']}; font-size: 8.8pt; font-weight: 600; }}
#Why {{ color: {C['muted']}; font-size: 8.8pt; }}
#RowAction {{ color: {C['text_soft']}; font-size: 8.8pt; background: transparent; border: none;
              padding: 2px 6px; border-radius: 6px; }}
#CompactTitle {{ font-size: 10.4pt; }}
#ReaderMeta {{ color: {C['muted']}; font-size: 8.3pt; font-weight: 600; letter-spacing: 0.9px; }}
#ReaderTitle {{ font-size: 23pt; font-weight: 700; letter-spacing: -0.6px; }}
#ReaderH2 {{ font-size: 13.5pt; font-weight: 650; letter-spacing: -0.2px; }}
#TopBar {{ background: {C['window_solid']}; border-bottom: 1px solid {C['hairline']}; }}
#Pill {{ background: {C['button']}; border: none; border-radius: 15px; padding: 7px 13px; font-size: 9.6pt; }}
#PillOn {{ background: {C['accent_soft']}; color: {C['accent']}; border: none; border-radius: 15px;
           padding: 7px 13px; font-size: 9.6pt; font-weight: 600; }}
#Star {{ color: {C['faint']}; font-size: 15pt; background: transparent; border: none; padding: 0 2px; }}
#StarOn {{ color: {C['warn']}; font-size: 15pt; background: transparent; border: none; padding: 0 2px; }}
#ParaHover {{ background: rgba(255, 255, 255, 6); border-radius: 8px; }}
#Original {{ color: {C['muted']}; font-size: 8.6pt; background: transparent; border: none; }}
#SearchBox {{ background: {C['input']}; border: 1px solid {C['input_border']}; border-radius: 12px;
              padding: 11px 16px; font-size: 13pt; }}
#Hint {{ color: {C['muted']}; font-size: 9pt; }}
#Translating {{ color: {C['muted']}; font-size: 9pt; }}
#Body {{ font-family: "Literata"; font-size: 12.8pt; }}
#Quote {{ border-left: 3px solid {C['accent']}; }}
#QuoteText {{ font-family: "Literata"; font-size: 12.8pt; font-style: italic; }}
QProgressBar#ReadProgress {{ background: transparent; border: none; }}
QProgressBar#ReadProgress::chunk {{ background: {C['accent']}; border-radius: 0; }}
"""
app.setStyleSheet(theme.stylesheet(C) + EXTRA)


# --------------------------------------------------------------- рисованные мелочи
def app_icon_pixmap(size: int = 64) -> QPixmap:
    """Иконка «Сводки»: тёмно-синий сквиркл, три строки и акцентная точка."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    rect = QRectF(s * 0.06, s * 0.05, s * 0.88, s * 0.88)
    shape = theme.squircle_path(rect)
    bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg.setColorAt(0.0, QColor("#2A3F66"))
    bg.setColorAt(0.55, QColor("#16223A"))
    bg.setColorAt(1.0, QColor("#0C1222"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawPath(shape)
    p.setPen(QPen(QColor(255, 255, 255, 34), max(1.0, s * 0.01)))
    p.setBrush(Qt.NoBrush)
    p.drawPath(shape)
    pen = QPen(QColor("#E9EDF4"), s * 0.07, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    for y, x2 in ((0.36, 0.72), (0.5, 0.64), (0.64, 0.56)):
        p.drawLine(QPointF(s * 0.3, s * y), QPointF(s * x2, s * y))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawEllipse(QPointF(s * 0.72, s * 0.64), s * 0.07, s * 0.07)
    p.end()
    return pm


def nav_icon(name: str, color: str, size: int = 18) -> QIcon:
    if name == "settings":
        return theme.nav_icon("settings", color, size)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    s = size
    if name == "feed":
        for y, x2 in ((0.3, 0.8), (0.5, 0.8), (0.7, 0.58)):
            p.drawLine(QPointF(s * 0.2, s * y), QPointF(s * x2, s * y))
    elif name == "search":
        p.drawEllipse(QPointF(s * 0.44, s * 0.44), s * 0.24, s * 0.24)
        p.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.8, s * 0.8))
    elif name == "saved":
        r = QRectF(s * 0.3, s * 0.18, s * 0.4, s * 0.64)
        p.drawLine(r.topLeft(), r.topRight())
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.drawLine(r.topRight(), r.bottomRight())
        p.drawLine(r.bottomLeft(), QPointF(r.center().x(), r.bottom() - s * 0.16))
        p.drawLine(r.bottomRight(), QPointF(r.center().x(), r.bottom() - s * 0.16))
    p.end()
    return QIcon(pm)


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class ElideLabel(QLabel):
    """Однострочная подпись: не влезает — многоточие в конце."""

    def __init__(self, text: str, name: str = ""):
        super().__init__(text)
        self.full = text
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full, Qt.ElideRight, self.width()))


def link(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Link")
    return b


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        else:
            lay.addWidget(w)
    return lay


# --------------------------------------------------------------- каркас окна
class Backdrop(QWidget):
    def __init__(self, sidebar: bool = True):
        super().__init__()
        self.sidebar = sidebar

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        if self.sidebar:
            p.fillRect(0, 0, SIDEBAR_W, self.height(), W.pc("sidebar_solid"))
            p.fillRect(SIDEBAR_W, 0, self.width() - SIDEBAR_W, self.height(), W.pc("window_solid"))
            p.fillRect(SIDEBAR_W, 0, 1, self.height(), W.pc("hairline"))
        else:
            p.fillRect(self.rect(), W.pc("window_solid"))
        p.end()


def sidebar(active: str) -> QWidget:
    side = QWidget()
    side.setFixedWidth(SIDEBAR_W)
    lay = QVBoxLayout(side)
    lay.setContentsMargins(12, 20, 12, 16)
    lay.setSpacing(2)
    logo = QLabel()
    logo.setPixmap(app_icon_pixmap(32))
    lay.addLayout(hbox(logo, label("Сводка", "AppTitle"), None, spacing=10, margins=(6, 0, 0, 0)))
    lay.addSpacing(20)
    for key, text in (("feed", "Лента"), ("search", "Поиск"), ("saved", "Сохранённое"), ("settings", "Настройки")):
        b = QPushButton(f" {text}")
        b.setObjectName("Nav")
        b.setCheckable(True)
        b.setChecked(key == active)
        b.setIcon(nav_icon(key, C["accent"] if key == active else C["muted"]))
        lay.addWidget(b)
    lay.addStretch(1)
    status = label("Собрано в 08:07 · следующий сбор в 19:07", "SidebarFooter", wrap=True)
    status.setContentsMargins(8, 0, 4, 6)
    lay.addWidget(status)
    lay.addLayout(hbox(link("Как пользоваться"), None, label("v0.1", "SidebarFooter"), margins=(4, 0, 0, 0)))
    return side


def page_column(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    outer = QWidget()
    row = QHBoxLayout(outer)
    row.setContentsMargins(36, 30, 36, 40)
    column = QWidget()
    column.setMaximumWidth(CONTENT_MAX_W)
    lay = QVBoxLayout(column)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(22)
    row.addStretch(1)
    row.addWidget(column, 100)
    row.addStretch(1)
    texts = QVBoxLayout()
    texts.setSpacing(4)
    texts.addWidget(label(title, "PageTitle"))
    if subtitle:
        texts.addWidget(label(subtitle, "PageSubtitle"))
    head = QHBoxLayout()
    head.addLayout(texts, 1)
    head.addWidget(W.HelpButton(), 0, Qt.AlignTop)
    lay.addLayout(head)
    return outer, lay


def window(active: str, build) -> QWidget:
    root = Backdrop(sidebar=True)
    lay = QHBoxLayout(root)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(sidebar(active))
    content, col = page_column(*build.header)
    build(col)
    col.addStretch(1)
    lay.addWidget(content, 1)
    return root


# --------------------------------------------------------------- Лента
def feed_row(topic, source, ago, minutes, title, summary, *, hover=False, read=False, explore=False, why=""):
    row = QFrame()
    row.setObjectName("RowHover" if hover else "Row")
    lay = QVBoxLayout(row)
    lay.setContentsMargins(14, 12, 14, 13)
    lay.setSpacing(5)
    meta = [label(topic, "MetaTopic"), label(f"·  {source}  ·  {ago}  ·  {minutes} мин", "Meta")]
    if explore:
        badge = label("Разведка", "Badge")
        meta.insert(0, badge)
    meta.append(None)
    if hover:
        for text in ("👍", "👎", "Сохранить", "⋯"):
            b = QPushButton(text)
            b.setObjectName("RowAction")
            meta.append(b)
    lay.addLayout(hbox(*meta, spacing=6))
    t = label(title, "FeedTitleRead" if read else "FeedTitle", wrap=True)
    lay.addWidget(t)
    if summary:
        lay.addWidget(label(summary, "FeedSummary", wrap=True))
    if why:
        lay.addSpacing(2)
        lay.addWidget(label(why, "Why", wrap=True))
    return row


def compact_row(title, source, minutes, topic, meta=None):
    row = QFrame()
    row.setObjectName("Row")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(12)
    lay.addWidget(ElideLabel(title, "CompactTitle"), 1)
    lay.addWidget(label(meta or f"{topic}  ·  {source}  ·  {minutes} мин", "Meta"))
    return row


def group_of(rows, title="") -> QWidget:
    g = W.Group(title)
    g.rows.setContentsMargins(4, 4, 4, 4)
    for r in rows:
        g.add_widget(r)
    return g


def build_feed(col: QVBoxLayout) -> None:
    col.addWidget(group_of([
        feed_row("ИИ-агенты", "Open Source Weekly", "2 ч", 7,
                 "Почему ИИ-агенты до сих пор спотыкаются на простых веб-формах",
                 "Авторы прогнали несколько открытых агентов через сотню реальных форм. Чаще всего ломаются "
                 "выпадающие списки и подтверждения по почте; разбор показывает, что помогает."),
        feed_row("Разработка", "Practical AI", "4 ч", 11,
                 "Как маленькая команда перевела поиск по документации на локальные эмбеддинги",
                 "Подробный разбор с цифрами: что дало переход, сколько стоил и где пришлось вернуть "
                 "классический полнотекстовый поиск.", hover=True,
                 why="Почему здесь: вы дочитали 4 похожих разбора · свежая · важность высокая"),
        feed_row("Наука", "Хабр", "6 ч", 5,
                 "Новый метод сжатия моделей почти не теряет качества на русском языке",
                 "Исследователи сравнили несколько способов квантования на русскоязычных задачах. "
                 "Таблицы и выводы — внутри.", explore=True),
    ], "Главное"))
    col.addWidget(group_of([
        compact_row("Что изменилось в новой версии популярного фреймворка для агентов", "Dev Notes", 4, "ИИ-агенты"),
        compact_row("Пять приёмов, которые ускоряют SQLite в десктопных приложениях", "Хабр", 9, "Разработка"),
        compact_row("Интервью: как устроена команда, которая делает открытый браузер", "Signal & Noise", 14,
                    "Технологии"),
        compact_row("Обзор: где сейчас дешевле всего запускать модели в облаке", "Practical AI", 6, "Инфраструктура"),
    ], "Ещё сегодня"))


build_feed.header = ("Лента", "Вторник, 7 октября · 12 новых")


# --------------------------------------------------------------- Поиск
def build_search(col: QVBoxLayout) -> None:
    box = QLineEdit()
    box.setObjectName("SearchBox")
    box.setText("локальные эмбеддинги для поиска")
    col.addWidget(box)
    col.addWidget(label("В библиотеке нашлось 2 статьи. Нужно свежее — Claude поищет в интернете, "
                        "переведёт и добавит сюда.", "Hint", wrap=True))
    col.addWidget(group_of([
        feed_row("Разработка", "Practical AI", "вчера", 11,
                 "Как маленькая команда перевела поиск по документации на локальные эмбеддинги",
                 "…переход на <b>локальные эмбеддинги</b> сократил задержку поиска втрое, но для точных "
                 "названий пришлось оставить полнотекстовый индекс…", read=True),
        feed_row("Разработка", "Хабр", "3 дня", 8,
                 "Гибридный поиск: когда смысловой индекс проигрывает обычному",
                 "…классический индекс по-прежнему выигрывает на кодах ошибок и именах функций…"),
    ], "В библиотеке"))
    btn = QPushButton("  Искать в интернете с Claude")
    btn.setObjectName("Primary")
    col.addLayout(hbox(btn, label("займёт около минуты, результат появится в Ленте и здесь", "Hint"), None,
                       spacing=12))
    col.addWidget(group_of([
        compact_row("ИИ-агенты в браузере", "", 0, "", meta="2 новых  ·  проверено в 08:07"),
        compact_row("Производительность SQLite", "", 0, "", meta="нет нового  ·  проверено в 08:07"),
    ], "Сохранённые запросы — проверяются при каждом сборе"))


build_search.header = ("Поиск", "По вашей библиотеке и, если нужно, по интернету")


# --------------------------------------------------------------- Читалка
def paragraph(html: str, hover: bool = False, faded: bool = False) -> QWidget:
    wrap = QFrame()
    wrap.setObjectName("ParaHover" if hover else "")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(10 if hover else 0, 6 if hover else 0, 10 if hover else 0, 6 if hover else 0)
    color = C["faint"] if faded else READ_COLOR
    lab = QLabel(f'<div style="line-height:118%; color:{color};">{html}</div>')
    lab.setObjectName("Body")
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(lab, 1)
    if hover:
        orig = QPushButton("EN")
        orig.setObjectName("Original")
        orig.setToolTip("Показать оригинал абзаца")
        lay.addWidget(orig, 0, Qt.AlignTop)
    return wrap


def reader_shell(progress: int) -> tuple[QWidget, QVBoxLayout]:
    root = Backdrop(sidebar=False)
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    bar = QFrame()
    bar.setObjectName("TopBar")
    bl = QHBoxLayout(bar)
    bl.setContentsMargins(20, 10, 20, 10)
    back = link("‹  Лента")
    seg = W.Segmented(["Перевод", "Оригинал"])
    save = QPushButton("Сохранить")
    bl.addWidget(back)
    bl.addStretch(1)
    bl.addWidget(label("Open Source Weekly  ·  осталось 4 мин", "Meta"))
    bl.addStretch(1)
    bl.addWidget(seg)
    bl.addSpacing(8)
    bl.addWidget(save)
    outer.addWidget(bar)
    pb = QProgressBar()
    pb.setObjectName("ReadProgress")
    pb.setTextVisible(False)
    pb.setFixedHeight(2)
    pb.setValue(progress)
    outer.addWidget(pb)
    body = QWidget()
    row = QHBoxLayout(body)
    row.setContentsMargins(36, 34, 36, 48)
    column = QWidget()
    column.setFixedWidth(READER_W)
    col = QVBoxLayout(column)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(18)
    row.addStretch(1)
    row.addWidget(column)
    row.addStretch(1)
    outer.addWidget(body, 1)
    return root, col


def build_reader_top() -> QWidget:
    root, col = reader_shell(34)
    col.addWidget(label("ИИ-АГЕНТЫ  ·  OPEN SOURCE WEEKLY  ·  7 ОКТЯБРЯ  ·  7 МИН ЧТЕНИЯ", "ReaderMeta"))
    col.addWidget(label("Почему ИИ-агенты до сих пор спотыкаются на простых веб-формах", "ReaderTitle", wrap=True))
    short = W.Card("", "", padding=16)
    short.add(label("КОРОТКО", "SectionLabel"))
    for line in ("Авторы проверили агентов на сотне реальных форм — от записи к врачу до заказа справок.",
                 "Больше всего ошибок дают выпадающие списки, календари и подтверждение по почте.",
                 "Помогает «чтение» страницы как дерева доступности, а не как картинки."):
        short.add(label("•  " + line, "Soft", wrap=True))
    col.addWidget(short)
    col.addWidget(paragraph(
        "Демонстрации ИИ-агентов обычно выглядят безупречно: агент открывает сайт, находит нужную "
        "кнопку и за минуту оформляет заказ. Но стоит отправить его на обычный сайт районной поликлиники "
        "или городской библиотеки, и уверенность быстро заканчивается."))
    col.addWidget(paragraph(
        "Мы собрали сто настоящих форм и прогнали через них несколько открытых агентов. Задания были "
        "намеренно скучными: записаться на приём, продлить читательский билет, поменять адрес доставки. "
        "Именно такие задачи люди хотят отдать машине в первую очередь.", hover=True))
    col.addWidget(label("Где ломается", "ReaderH2"))
    col.addWidget(paragraph(
        "Чаще всего агенты ошибались в выпадающих списках, которые сайт рисует сам, без стандартного "
        "элемента выбора. Агент видит текст, щёлкает по нему, но список не раскрывается — и дальше "
        "он начинает угадывать."))
    quote = QFrame()
    quote.setObjectName("Quote")
    ql = QVBoxLayout(quote)
    ql.setContentsMargins(16, 2, 0, 2)
    q = QLabel(f'<div style="line-height:115%; color:{C["text_soft"]};">«Агенту не хватает не ума, '
               'а терпения: человек подождёт, пока календарь прогрузится, а агент решает, что его нет»</div>')
    q.setObjectName("QuoteText")
    q.setWordWrap(True)
    ql.addWidget(q)
    col.addWidget(quote)
    col.addWidget(paragraph(
        "Второе слабое место — подтверждение по почте. Агенту нужно уйти с сайта, найти письмо, "
        "вернуться и ввести код, а многие из них к этому моменту уже забывают, что делали.", faded=True))
    pb = QProgressBar()
    pb.setFixedHeight(4)
    pb.setTextVisible(False)
    pb.setValue(45)
    col.addLayout(hbox(label("Переводится… 4 из 9 абзацев", "Translating"), pb, None, spacing=12))
    col.addStretch(1)
    return root


def build_reader_end() -> QWidget:
    root, col = reader_shell(100)
    col.addWidget(paragraph(
        "Хорошая новость в том, что многие ошибки исправляются без новых моделей: достаточно, чтобы "
        "агент читал страницу так же, как её читает экранный диктор, — по дереву доступности, а не по "
        "картинке. Сайты, которые сделаны удобными для незрячих людей, оказались удобными и для агентов."))
    col.addWidget(W.divider())
    pills = []
    for text, on in (("🔥 Круто", False), ("👍 Интересно", True), ("👎 Не моя тема", False),
                     ("👀 Следить", False), ("⋯", False)):
        b = QPushButton(text)
        b.setObjectName("PillOn" if on else "Pill")
        pills.append(b)
    col.addLayout(hbox(*pills, None, spacing=6))
    survey = W.Card("", "", padding=16)
    stars = [QPushButton("★") for _ in range(5)]
    for i, s in enumerate(stars):
        s.setObjectName("StarOn" if i < 4 else "Star")
    survey.add_layout(hbox(label("Стоило потраченного времени?", "CardTitle"), None, *stars, spacing=0))
    survey.add(label("Короткий вопрос появляется не всегда — так лента учится отличать «прочитал» от "
                     "«было полезно».", "Muted", wrap=True))
    col.addWidget(survey)
    ask = QLineEdit()
    ask.setPlaceholderText("Спросить Claude об этой статье…")
    go = QPushButton("Спросить")
    go.setObjectName("Primary")
    col.addLayout(hbox(ask, go, spacing=8))
    col.addWidget(group_of([
        compact_row("Как браузерные агенты читают дерево доступности", "Dev Notes", 6, "ИИ-агенты"),
        compact_row("Тесты на настоящих сайтах: почему лабораторные цифры врут", "Signal & Noise", 9, "ИИ-агенты"),
        compact_row("Доступность сайтов как побочный плюс для автоматизации", "Хабр", 7, "Разработка"),
    ], "Похожее"))
    col.addStretch(1)
    return root


# --------------------------------------------------------------- рендер
def shoot(widget: QWidget, name: str, w: int = 1280, h: int = 820) -> None:
    widget.resize(w, h)
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(OUT, name))
    print("saved", name)


shoot(window("feed", build_feed), "01-feed.png")
shoot(build_reader_top(), "02-reader.png", h=1180)
shoot(build_reader_end(), "03-reader-end.png", h=900)
shoot(window("search", build_search), "04-search.png")
