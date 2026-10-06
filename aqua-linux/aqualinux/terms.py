"""Встроенная база терминов: как распознавание пишет английские названия кириллицей
(«гитхаб», «докером», «эс кью эль») → как их пишут (GitHub, Docker, SQL).

База собирается из открытых источников скриптом tools/build_terms.py и лежит в
aqualinux/data/terms_ru.tsv.gz. Формат строк:
    вариант<TAB>Термин      — вариант в нижнем регистре, «ё» → «е», слова через пробел
    !словоформа             — склонённая форма, совпадающая с обычным русским словом
                              («расту» от «раст»): её НЕ заменяем
Падежи не хранятся: «гитхабе», «докером», «фигмой» сводятся к основе при поиске.
"""
from __future__ import annotations

import gzip
import logging
import re
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent / "data" / "terms_ru.tsv.gz"

# Окончания для склонения заимствованных названий (по убыванию длины).
ENDINGS = ("ами", "ями", "ах", "ях", "ам", "ям", "ов", "ев", "ом", "ем", "ой", "ою", "ей",
           "а", "я", "у", "ю", "е", "ы", "и")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
_SEP_OK = re.compile(r"^[ \t\-]+$")


def norm(word: str) -> str:
    return word.lower().replace("ё", "е")


def stems(form: str) -> list[str]:
    """Возможные основы склонённой формы: «докером» → «докер», «фигме» → «фигма»,
    «спотифаем» → «спотифай», «экселе» → «эксель»."""
    out = []
    for ending in ENDINGS:
        if form.endswith(ending) and len(form) - len(ending) >= 3:
            stem = form[: -len(ending)]
            out.extend((stem, stem + "а", stem + "й", stem + "ь", stem + "я", stem + "о"))
    return out


def declensions(base: str) -> list[str]:
    """Все формы, которые поиск сведёт к этой основе (нужно сборщику, чтобы отметить
    формы, совпадающие с русскими словами)."""
    if len(base) < 4 or not re.fullmatch(r"[а-яе]+", base):
        return []
    if base[-1] in "аяйьо":
        roots = [base[:-1]]
    else:
        roots = [base]
    forms = set()
    for root in roots:
        if len(root) < 3:
            continue
        for ending in ENDINGS:
            form = root + ending
            if form != base and base in stems(form):
                forms.add(form)
    return sorted(forms)


class TermIndex:
    def __init__(self):
        self.exact: dict[tuple, str] = {}
        self.blocked: set[str] = set()
        self.max_words = 1
        self.loaded = False

    def __len__(self) -> int:
        return len(set(self.exact.values()))

    def load(self, path: Path = DATA_FILE) -> "TermIndex":
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("!"):
                        self.blocked.add(line[1:])
                        continue
                    variant, _, term = line.partition("\t")
                    if not term:
                        continue
                    key = tuple(variant.split(" "))
                    self.exact.setdefault(key, term)
                    self.max_words = max(self.max_words, len(key))
            self.loaded = True
            log.info("Встроенная база терминов: %d терминов, %d вариантов", len(self), len(self.exact))
        except FileNotFoundError:
            log.warning("Нет файла базы терминов %s", path)
        except Exception:  # noqa: BLE001 — повреждённый файл не должен ломать диктовку
            log.exception("Не удалось прочитать базу терминов")
        return self

    def _lookup(self, words: tuple) -> Optional[str]:
        term = self.exact.get(words)
        if term is not None:
            return term
        last = words[-1]
        if last in self.blocked or len(last) < 5:
            return None
        for stem in stems(last):
            term = self.exact.get(words[:-1] + (stem,))
            if term is not None and len(stem) >= 4:
                return term
        return None

    def apply(self, text: str) -> tuple[str, list[str]]:
        """Заменить варианты на термины. Возвращает (текст, найденные термины)."""
        if not self.exact or not text:
            return text, []
        matches = list(_WORD.finditer(text))
        if not matches:
            return text, []
        words = [norm(m.group(0)) for m in matches]
        out, found, pos, i = [], [], 0, 0
        while i < len(matches):
            hit = None
            for n in range(min(self.max_words, len(matches) - i), 0, -1):
                # Слова одного названия разделены только пробелом или дефисом (не точкой, не запятой).
                if n > 1 and any(not _SEP_OK.match(text[matches[k].end():matches[k + 1].start()])
                                 for k in range(i, i + n - 1)):
                    continue
                key = tuple(words[i:i + n])
                if any(re.search(r"[a-z]", w) for w in key):
                    continue      # уже латиница — не трогаем
                term = self._lookup(key)
                if term is not None:
                    hit = (n, term)
                    break
            if hit is None:
                i += 1
                continue
            n, term = hit
            start, end = matches[i].start(), matches[i + n - 1].end()
            out.append(text[pos:start])
            out.append(term)
            found.append(term)
            pos = end
            i += n
        out.append(text[pos:])
        return "".join(out), list(dict.fromkeys(found))


_index: Optional[TermIndex] = None
_lock = threading.Lock()


def index() -> TermIndex:
    """Общая база (загружается один раз, ~0,1–0,3 с)."""
    global _index
    with _lock:
        if _index is None:
            _index = TermIndex().load()
        return _index
