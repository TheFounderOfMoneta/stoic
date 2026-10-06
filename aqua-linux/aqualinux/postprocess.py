"""Постобработка распознанного текста: паразиты, голосовые команды, словарь, сниппеты."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from . import terms as builtin_terms
from .numbers import normalize as normalize_numbers

# Звуки-заминки, которые никогда не бывают смысловыми словами.
FILLERS_RU = r"э+|э-э+|эм+|эээм|м+|мм+|хм+|а-а+|ммм|угу-угу"
FILLERS_EN = r"um+|uh+|erm+|hmm+|uhm+"
_FILLER_RE = re.compile(
    rf"(?<![\w-])(?:{FILLERS_RU}|{FILLERS_EN})(?![\w-])[,.…]?\s*", re.IGNORECASE)

VOICE_COMMANDS = [
    (re.compile(r",?\s*\bнов(?:ый|ого) абзац\b[,.]?\s*", re.IGNORECASE), "\n\n"),
    (re.compile(r",?\s*\bнов(?:ая|ой|ую) строк[аиу]\b[,.]?\s*", re.IGNORECASE), "\n"),
    (re.compile(r",?\s*\bс новой строки\b[,.]?\s*", re.IGNORECASE), "\n"),
    (re.compile(r",?\s*\bnew paragraph\b[,.]?\s*", re.IGNORECASE), "\n\n"),
    (re.compile(r",?\s*\bnew line\b[,.]?\s*", re.IGNORECASE), "\n"),
]

_SENTENCE_END = re.compile(r"([.!?])(\s+)([a-zа-яё])")
ABBREVIATIONS = {
    "т", "т.е", "т.к", "т.д", "т.п", "т.н", "е", "к", "д", "п", "н", "см", "г", "гг", "ул", "пр", "просп",
    "пл", "пер", "кв", "стр", "рис", "табл", "руб", "коп", "тыс", "млн", "млрд", "др", "им", "акад",
    "проф", "доц", "гл", "ст", "ср", "напр", "мин", "сек", "ч", "кг", "км", "см", "мм", "л", "шт", "с",
    "вкл", "искл", "англ", "рус", "лат", "etc", "e.g", "i.e", "vs", "mr", "mrs", "dr", "no",
}

_LAT2CYR = [
    ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ya", "я"),
    ("yu", "ю"), ("yo", "ё"), ("ph", "ф"), ("th", "т"), ("ck", "к"), ("qu", "кв"), ("oo", "у"),
    ("ee", "и"), ("ea", "и"), ("ou", "ау"), ("ai", "ей"), ("ay", "ей"), ("tion", "шн"),
    ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"),
    ("h", "х"), ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"),
    ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"),
    ("v", "в"), ("w", "в"), ("x", "кс"), ("y", "и"), ("z", "з"),
]
_LAT2CYR.sort(key=lambda p: -len(p[0]))


def translit_to_cyrillic(word: str) -> str:
    """Грубая фонетическая транслитерация латиницы — только для сравнения слов."""
    s = word.lower()
    out = []
    i = 0
    while i < len(s):
        for lat, cyr in _LAT2CYR:
            if s.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.lower().replace("ё", "е"))


@dataclass
class DictEntry:
    term: str                 # как писать
    sounds_like: list         # как модель обычно слышит (необязательно)
    fuzzy: bool = True


class TextProcessor:
    def __init__(self):
        self.entries: list[DictEntry] = []
        self.replacements: list[dict] = []
        self._compiled: list[tuple[re.Pattern, str, bool]] = []

    def load(self, entries: list[dict], replacements: list[dict]) -> None:
        """entries: [{term, sounds_like, fuzzy}]; replacements: [{from, to, preserve_case, strip_punct}]."""
        from .storage import clean_replacements, clean_terms
        self.entries = [DictEntry(e["term"], e["sounds_like"], e["fuzzy"]) for e in clean_terms(entries)]
        self.replacements = clean_replacements(replacements)
        compiled = []
        for rep in self.replacements:
            tail = r"[.!?…]*" if rep.get("strip_punct", True) else ""
            pattern = re.compile(rf"(?<!\w){re.escape(rep['from'].strip())}(?!\w){tail}", re.IGNORECASE)
            compiled.append((pattern, rep.get("to", ""), rep.get("preserve_case", True)))
        for entry in self.entries:
            for variant in entry.sounds_like:
                compiled.append((re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE),
                                 entry.term, True))
        compiled.sort(key=lambda p: -len(p[0].pattern))
        self._compiled = compiled

    def vocabulary(self) -> list[str]:
        return [e.term for e in self.entries]

    # ------------------------------------------------------------- шаги
    def remove_fillers(self, text: str) -> str:
        text = _FILLER_RE.sub("", text)
        text = re.sub(r"\s+([,.!?…:;])", r"\1", text)
        text = re.sub(r"^[,.\s]+", "", text)
        text = re.sub(r",\s*,", ",", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        return text

    def voice_commands(self, text: str) -> str:
        for pattern, repl in VOICE_COMMANDS:
            text = pattern.sub(repl, text)
        # Заглавная буква после переноса строки.
        text = re.sub(r"\n(\s*)([a-zа-яё])", lambda m: "\n" + m.group(1) + m.group(2).upper(), text)
        return text.strip(" ")

    def apply_dictionary(self, text: str, fuzzy: bool = True, threshold: float = 0.84) -> str:
        for pattern, dst, preserve in self._compiled:
            def repl(match, dst=dst, preserve=preserve):
                if not preserve and dst[:1].islower() and match.group(0)[:1].isupper():
                    return dst[:1].upper() + dst[1:]
                return dst
            text = pattern.sub(repl, text)
        if fuzzy and self.entries:
            text = self._fuzzy(text, threshold)
        return text

    def _fuzzy(self, text: str, threshold: float) -> str:
        """Заменяет похожие на термины слова/пары слов (кубернетис → Kubernetes)."""
        tokens = re.findall(r"\w[\w'-]*|\W+", text)
        word_idx = [i for i, t in enumerate(tokens) if re.match(r"\w", t)]
        targets = []
        for entry in self.entries:
            if not entry.fuzzy or len(_norm(entry.term)) < 4:
                continue
            forms = {_norm(entry.term), _norm(translit_to_cyrillic(entry.term))}
            n_words = max(1, len(entry.term.split()))
            targets.append((entry, forms, n_words))
        if not targets:
            return text
        replaced = set()
        for pos in range(len(word_idx)):
            if word_idx[pos] in replaced:
                continue
            best = None  # (score, -span, entry, idxs)
            for entry, forms, n_words in targets:
                for span in {n_words, n_words + 1, max(1, n_words - 1)}:
                    if pos + span > len(word_idx):
                        continue
                    idxs = word_idx[pos:pos + span]
                    if any(i in replaced for i in idxs):
                        continue
                    words = [tokens[i] for i in idxs]
                    # Лишнее слово в окне не должно быть предлогом/союзом («на гитхаб»).
                    if span > n_words and min(len(w) for w in words) < 3:
                        continue
                    if span == 1 and words[0] == entry.term:
                        continue
                    candidate = _norm("".join(words))
                    if len(candidate) < 4:
                        continue
                    score = max(difflib.SequenceMatcher(None, candidate, f).ratio() for f in forms)
                    # Короткие слова легко спутать («доски» ≈ «Docs») — порог выше.
                    need = threshold + (0.08 if len(candidate) < 6 else 0.0)
                    if score >= need and (best is None or (score, -span) > best[:2]):
                        best = (score, -span, entry, idxs)
            if best is not None:
                _, _, entry, idxs = best
                first, last = idxs[0], idxs[-1]
                tokens[first] = entry.term
                for i in range(first + 1, last + 1):
                    tokens[i] = ""
                replaced.update(range(first, last + 1))
        return "".join(tokens)

    def capitalize_sentences(self, text: str) -> str:
        """Заглавная буква после точки, «!» и «?» («Всем привет. я хочу» → «…Я хочу»),
        кроме сокращений («т. е.», «см.», «ул.», «руб.»…)."""
        def repl(m: re.Match) -> str:
            before = text[:m.start() + 1]
            word = re.search(r"([\w.]+)\.$", before)
            if m.group(1) == "." and word and word.group(1).lower().replace("ё", "е") in ABBREVIATIONS:
                return m.group(0)
            return m.group(1) + m.group(2) + m.group(3).upper()
        return _SENTENCE_END.sub(repl, text)

    def finalize(self, text: str) -> str:
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" +([,.!?…:;])", r"\1", text)
        return text.strip()

    def apply_terms(self, text: str, settings) -> tuple[str, list[str]]:
        """Встроенная база терминов (GitHub, Docker, SQL…), после словаря пользователя."""
        if not settings.get("text.builtin_terms", True):
            return text, []
        try:
            return builtin_terms.index().apply(text)
        except Exception:  # noqa: BLE001 — база не должна ломать диктовку
            return text, []

    def pre(self, raw: str, settings) -> tuple[str, list[str]]:
        """Подготовка к ИИ: словарь пользователя и встроенные термины (без остальной обработки).
        Возвращает (текст, термины, найденные в нём) — их подсказываем ИИ."""
        text = raw.strip()
        if not text:
            return "", []
        text = self.apply_dictionary(text, settings.get("text.fuzzy_dictionary", True),
                                     float(settings.get("text.fuzzy_threshold", 0.84)))
        text, found = self.apply_terms(text, settings)
        user_terms = [e.term for e in self.entries if e.term in text]
        return text, list(dict.fromkeys(user_terms + found))

    def process(self, raw: str, settings) -> str:
        text = raw.strip()
        if not text:
            return ""
        if settings.get("text.remove_fillers", True):
            text = self.remove_fillers(text)
        if settings.get("text.voice_commands", True):
            text = self.voice_commands(text)
        text = self.apply_dictionary(text, settings.get("text.fuzzy_dictionary", True),
                                     float(settings.get("text.fuzzy_threshold", 0.84)))
        text = self.apply_terms(text, settings)[0]
        if settings.get("text.numbers", True):
            text = normalize_numbers(text, settings.get("text.number_style", "sign") or "sign")
        text = self.capitalize_sentences(text)
        return self.finalize(text)


def count_words(text: str) -> int:
    return len(re.findall(r"\w+", text))


SEND_PHRASES = {"отправь", "отправить", "отправляй", "send it", "send"}
MESSENGER_HINTS = ("telegram", "slack", "discord", "signal", "whatsapp", "element", "vk messenger",
                   "viber", "mattermost", "rocket.chat", "zulip", "teams", "max messenger")


def split_send_it(text: str) -> tuple[str, bool]:
    """«…Отправь.» последним предложением → (текст без команды, True)."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    if parts and _norm_phrase(parts[-1]) in SEND_PHRASES:
        rest = " ".join(parts[:-1]).strip()
        return rest, True
    # «…, отправь.» в конце предложения
    match = re.search(r"[,\s]+(отправь|отправить|send it)[.!]?$", text.strip(), re.IGNORECASE)
    if match and len(text) > match.end() - match.start() + 2:
        rest = text.strip()[:match.start()].rstrip(" ,")
        if rest and rest[-1] not in ".!?…":
            rest += "."
        return rest, True
    return text, False


def _norm_phrase(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def casual(text: str) -> str:
    """Casual Messaging: строчная первая буква и без точки в конце."""
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        text = text[0].lower() + text[1:]
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    return text


def is_messenger(wm_class: str, title: str) -> bool:
    hay = f"{wm_class} {title}".lower()
    return any(hint in hay for hint in MESSENGER_HINTS)
