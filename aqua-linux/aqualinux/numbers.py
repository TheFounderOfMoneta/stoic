"""Русские числительные → цифры и единое написание «номера».

Правила осторожные, чтобы не портить обычную речь:
  * после слов «номер», «№», «пункт», «глава», «страница», «версия»… число всегда цифрами
    («номер один» → «№ 1»);
  * составные числа из двух и больше слов — цифрами («двадцать пять» → «25»);
  * одиночные «два», «пять», «сто» в обычной фразе не трогаем («сто лет не виделись»).
"""
from __future__ import annotations

import re

UNITS = {"ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3,
         "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9}
TEENS = {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
         "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
        "семьдесят": 70, "восемьдесят": 80, "девяносто": 90}
HUNDREDS = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
            "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900}
SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тысячу": 1000,
          "миллион": 10 ** 6, "миллиона": 10 ** 6, "миллионов": 10 ** 6}

# Порядок внутри группы: сотни (3) → десятки (2) / «-надцать» (1.5) → единицы (1).
_RANK = {**{w: 1 for w in UNITS}, **{w: 1.5 for w in TEENS}, **{w: 2 for w in TENS},
         **{w: 3 for w in HUNDREDS}}
_VALUE = {**UNITS, **TEENS, **TENS, **HUNDREDS}

MARKERS = ("номер", "номера", "номеру", "номером", "пункт", "пункта", "пункте", "глава", "главы", "главе",
           "страница", "страницы", "странице", "страницу", "параграф", "раздел", "раздела", "статья",
           "статьи", "статью", "версия", "версии", "версию", "вариант", "варианта", "задача", "задачи",
           "задание", "задания", "упражнение", "шаг", "этап", "дом", "квартира", "кабинет", "этаж",
           "корпус", "подъезд", "палата", "аудитория", "маршрут", "автобус", "трамвай", "троллейбус",
           "уровень", "сезон", "серия", "урок", "лекция", "№")

def _parse(words: list[str]) -> tuple[int, int]:
    """Число из начала списка слов: (значение, сколько слов занято). (0, 0) — не число."""
    total = 0
    group = 0
    last_rank = 9.0
    last_scale = 10 ** 12
    used = 0
    for i, word in enumerate(words):
        w = word.lower().replace("ё", "е")
        if w in _RANK:
            rank = _RANK[w]
            # «двадцать пять» — да; «пять двадцать», «десять пять» — это уже два числа.
            if rank >= last_rank or (last_rank == 1.5 and rank <= 1.5) or (last_rank == 2 and rank == 1.5):
                break
            group += _VALUE[w]
            last_rank = rank
        elif w in SCALES:
            scale = SCALES[w]
            if scale >= last_scale:
                break
            total += (group or 1) * scale
            group = 0
            last_rank = 9.0
            last_scale = scale
        else:
            break
        used = i + 1
    if not used:
        return 0, 0
    return total + group, used


def _tokens(text: str) -> list[str]:
    return re.findall(r"\s+|[А-Яа-яЁёA-Za-z]+|\d+|.", text, re.UNICODE)


def words_to_digits(text: str) -> str:
    """Заменяет числительные на цифры по правилам из описания модуля."""
    toks = _tokens(text)
    out: list[str] = []
    i = 0
    prev_word = ""
    while i < len(toks):
        tok = toks[i]
        low = tok.lower().replace("ё", "е")
        if low in _RANK or low in SCALES:
            # Собираем последовательность «слово пробел слово…» только из числительных.
            j = i
            seq: list[str] = []
            count = 0
            while j < len(toks):
                t = toks[j].lower().replace("ё", "е")
                if t in _RANK or t in SCALES:
                    seq.append(toks[j])
                    count += 1
                    j += 1
                elif toks[j].isspace() and j + 1 < len(toks) and \
                        toks[j + 1].lower().replace("ё", "е") in (_RANK.keys() | SCALES.keys()):
                    seq.append(toks[j])
                    j += 1
                else:
                    break
            words = [w for w in seq if not w.isspace()]
            value, used = _parse(words)
            after_marker = prev_word in MARKERS
            # Одиночные «тысяча»/«миллион» без числа перед ними («тысяча извинений») не трогаем.
            standalone_scale = used == 1 and words[0].lower() in SCALES
            if used and not standalone_scale and (after_marker or used >= 2):
                # Сколько исходных токенов (с пробелами) заняли used слов.
                k, n = 0, 0
                while n < used:
                    if not seq[k].isspace():
                        n += 1
                    k += 1
                digits = f"{value:,}".replace(",", " ") if value >= 10000 else str(value)
                out.append(digits)
                i += k
                prev_word = digits
                continue
        out.append(tok)
        if not tok.isspace():
            prev_word = low
        i += 1
    return "".join(out)


_NO = re.compile(r"(?<![\w№])(?:No\.?|N°|№)\s*(?=\d)")
_NOMER = re.compile(r"(?<![\w№])([Нн]омер)\s+(?=\d)")


def normalize_numero(text: str, style: str = "sign") -> str:
    """«No 1», «№1», «номер 25» → единый вид: «№ 1» (style="sign") или «номер 1» (style="word")."""
    if style == "word":
        def to_word(m: re.Match) -> str:
            start = m.start()
            before = text[:start].rstrip()
            cap = not before or before[-1] in ".!?…\n"
            return "Номер " if cap else "номер "
        text = _NO.sub(to_word, text)
        return text
    text = _NO.sub("№ ", text)
    text = _NOMER.sub("№ ", text)
    return text


def normalize(text: str, style: str = "sign") -> str:
    return normalize_numero(words_to_digits(text), style)
