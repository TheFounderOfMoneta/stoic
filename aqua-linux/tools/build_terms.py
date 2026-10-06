#!/usr/bin/env python3
"""Сборка встроенной базы терминов aqualinux/data/terms_ru.tsv.gz из открытых источников.

Источники (скачиваются в кэш при сборке, в программу попадает только итоговая база):
  * tools/terms_curated.txt — ручной список (высший приоритет);
  * simple-icons (CC0) — ~3,5 тыс. брендов и сервисов;
  * github-linguist languages.yml (MIT) — языки программирования и форматы;
  * wikidict-ru en-ru_wiki.txt (CC0, из Wikidata) — какие технические названия русская
    Википедия пишет латиницей (software, programming language, operating system, Unix…),
    а какие — кириллицей (Яндекс, Твиттер: такие НЕ переводим в латиницу), плюс реальные
    кириллические написания;
  * CMUdict (BSD) — произношение английских слов → русская фонетическая запись;
  * частотные списки русских слов (hermitdave/FrequencyWords, hingston/russian) — ТОЛЬКО
    фильтр: вариант, совпадающий с обычным русским словом («прага», «руби», «сигнал»), отбрасывается.

Запуск:  python3 tools/build_terms.py [--cache DIR] [--out PATH]
"""
from __future__ import annotations

import argparse
import gzip
import itertools
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aqualinux.terms import declensions  # noqa: E402

SOURCES = {
    "simple-icons.json": "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/data/simple-icons.json",
    "languages.yml": "https://raw.githubusercontent.com/github-linguist/linguist/main/lib/linguist/languages.yml",
    "en-ru_wiki.txt": "https://raw.githubusercontent.com/open-dict-data/wikidict-ru/master/data/en-ru_wiki.txt",
    "cmudict.dict": "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict",
    "ru_50k.txt": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ru/ru_50k.txt",
    "ru_100k.txt": "https://raw.githubusercontent.com/hingston/russian/master/100000-russian-words.txt",
}
TECH_CATEGORIES = {
    "software", "programming language", "operating system", "Unix", "web browser", "computing", "file format",
    "microarchitecture", "game engine", "command", "video game", "video game series", "video game console",
    "text editor", "search engine", "website", "social network", "app", "application", "messaging app",
    "chatbot", "language model", "protocol", "programming", "computer", "web framework", "database",
    "streaming service", "instant messaging client", "smartphone", "processor", "GPU", "graphics card",
}
# Российские сервисы и бренды, которые по-русски пишут кириллицей, — в латиницу не переводим.
NATIVE_CYRILLIC = {
    "Yandex", "VK", "Vkontakte", "Sberbank", "Sber", "Aeroflot", "LADA", "Lada", "Avito", "Ozon", "MTS", "MegaFon",
    "Megafon", "Beeline", "Tinkoff", "T-Bank", "Kaspersky", "Kinopoisk", "Hexlet", "Habr", "Rambler", "Rostelecom",
    "Gazprom", "Russian Railways", "Tele2", "Yota", "Dodo Pizza", "Odnoklassniki", "Mail.ru", "Gosuslugi",
    "Twitter", "Kaspersky Lab", "1C", "1C Enterprise", "Rutube", "Dzen",
}

# ---------------------------------------------------------------- транскрипция
CONS = {"B": "б", "CH": "ч", "D": "д", "DH": "з", "F": "ф", "G": "г", "HH": "х", "JH": "дж", "K": "к", "L": "л",
        "M": "м", "N": "н", "NG": "нг", "P": "п", "R": "р", "S": "с", "SH": "ш", "T": "т", "TH": "т", "V": "в",
        "W": "в", "Y": "й", "Z": "з", "ZH": "ж"}
VOWELS = {"AA": ["а", "о"], "AE": ["э", "а"], "AH": ["а"], "AO": ["о"], "AW": ["ау"], "AY": ["ай"], "EH": ["е"],
          "ER": ["ер"], "EY": ["ей"], "IH": ["и"], "IY": ["и"], "OW": ["оу", "о"], "OY": ["ой"], "UH": ["у"],
          "UW": ["у", "ю"]}
IOTA = {"а": "я", "о": "ё", "у": "ю", "э": "е", "е": "е", "и": "йи", "оу": "йоу", "ер": "ер"}


def cmu_to_ru(phones: list[str], limit: int = 4) -> list[str]:
    """ARPAbet → кириллица по правилам англо-русской практической транскрипции (с вариантами)."""
    ph = [re.sub(r"\d", "", p) for p in phones]
    stress = [re.sub(r"\D", "", p) for p in phones]
    options: list[list[str]] = []
    i = 0
    while i < len(ph):
        p = ph[i]
        prev_is_cons = i > 0 and ph[i - 1] in CONS
        if p in VOWELS:
            vals = list(VOWELS[p])
            if p in ("EH", "AE") and (i == 0 or not prev_is_cons):
                vals = ["э"]
            if p == "AH" and stress[i] == "0" and i == len(ph) - 2 and ph[-1] in ("L", "M", "N") and prev_is_cons:
                vals = [""]                           # Google → гугл, Notion → ноушн
            if p == "UW" and not (i > 0 and ph[i - 1] in ("N", "D", "T", "L", "S", "Z")):
                vals = ["у"]
            options.append(vals)
        elif p == "Y" and i + 1 < len(ph) and ph[i + 1] in VOWELS:
            vowel = VOWELS[ph[i + 1]][0]
            if prev_is_cons:
                options.append([IOTA.get(vowel, vowel), vowel])       # YouTube: тью / ту
            else:
                options.append([IOTA.get(vowel, "й" + vowel)])
            i += 2
            continue
        elif p == "W":
            options.append(["в", "у"])
        elif p == "Z" and i == len(ph) - 1:
            options.append(["з", "с"])
        elif p == "L" and i == len(ph) - 1 and i > 0 and ph[i - 1] in VOWELS:
            options.append(["л", "ль"])
        elif p == "ER":
            options.append(["ер"])
        elif p == "R" and i > 0 and ph[i - 1] in ("AA", "AO", "EH", "IH", "UH", "AH") and \
                (i == len(ph) - 1 or ph[i + 1] in CONS):
            options.append(["р", ""])
        else:
            options.append([CONS.get(p, "")])
        i += 1
    out = []
    for combo in itertools.product(*options):
        word = "".join(combo)
        if word and word not in out:
            out.append(word)
        if len(out) >= limit:
            break
    return out


SPELL = [
    ("tion", "шн"), ("sion", "жн"), ("ture", "чер"), ("sch", "ш"), ("tch", "ч"), ("igh", "ай"), ("ch", "ч"),
    ("sh", "ш"), ("th", "т"), ("ph", "ф"), ("ck", "к"), ("qu", "кв"), ("wh", "в"), ("ee", "и"), ("oo", "у"),
    ("ea", "и"), ("ai", "ей"), ("ay", "ей"), ("oi", "ой"), ("oy", "ой"), ("ou", "ау"), ("ow", "оу"), ("au", "о"),
    ("aw", "о"), ("ew", "ью"), ("ue", "ю"), ("x", "кс"), ("j", "дж"), ("q", "к"), ("w", "в"), ("h", "х"),
    ("b", "б"), ("d", "д"), ("f", "ф"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("p", "п"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("v", "в"), ("z", "з"), ("a", "а"), ("i", "и"), ("o", "о"), ("u", "у"),
]


def spell_to_ru(word: str, soft_g: bool, u_as_a: bool, y_as_ai: bool) -> str:
    """Чтение «по буквам», как часто произносят названия: Figma → фигма, Ollama → оллама."""
    w = word.lower()
    if w.startswith("kn"):
        w = w[1:]
    if w.startswith("wr"):
        w = w[1:]
    out, i = [], 0
    vowels = "aeiouy"
    while i < len(w):
        ch = w[i]
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch == "x" and nxt == "c" and i + 2 < len(w) and w[i + 2] in "eiy":
            out.append("кс"); i += 2; continue
        if ch == "c":
            out.append("с" if nxt in "eiy" else "к"); i += 1; continue
        if ch == "g":
            out.append("дж" if soft_g and nxt in "eiy" else "г"); i += 1; continue
        if ch == "e":
            if i == len(w) - 1 and len(w) > 3 and w[i - 1] not in vowels:
                i += 1; continue                       # немая «e» в конце
            out.append("э" if i == 0 or w[i - 1] in vowels else "е"); i += 1; continue
        if ch == "y":
            if i == 0 and nxt in vowels:
                out.append("й"); i += 1; continue
            out.append("ай" if y_as_ai and i == len(w) - 1 else "и"); i += 1; continue
        if ch == "u" and u_as_a and 0 < i < len(w) - 1 and w[i - 1] not in vowels and nxt not in vowels:
            out.append("а"); i += 1; continue
        for lat, cyr in SPELL:
            if w.startswith(lat, i):
                out.append(cyr); i += len(lat); break
        else:
            i += 1
    text = "".join(out)
    text = re.sub(r"й([аоуэ])", lambda m: {"а": "я", "о": "ё", "у": "ю", "э": "е"}[m.group(1)], text)
    return text


LETTERS_EN = {"a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
              "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
              "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл ю", "x": "икс", "y": "уай", "z": "зед",
              "2": "ту", "3": "три", "4": "фо", "5": "пять", "0": "ноль", "1": "уан", "6": "шесть", "7": "семь",
              "8": "эйт", "9": "девять"}
LETTERS_RU = {"a": "а", "b": "бэ", "c": "цэ", "d": "дэ", "e": "е", "f": "эф", "g": "гэ", "h": "аш", "i": "и",
              "j": "жи", "k": "ка", "l": "эль", "m": "эм", "n": "эн", "o": "о", "p": "пэ", "q": "ку", "r": "эр",
              "s": "эс", "t": "тэ", "u": "у", "v": "вэ", "w": "дубль вэ", "x": "икс", "y": "игрек", "z": "зэт"}
SHORT_WORDS_RU = {"а", "и", "о", "у", "е", "я", "в", "к", "с", "ты", "не", "ну", "да"}


def letter_variants(abbr: str) -> list[str]:
    chars = [c for c in abbr.lower() if c.isalnum()]
    out = []
    for table in (LETTERS_EN, LETTERS_RU):
        if not all(c in table for c in chars):
            continue
        names = [table[c] for c in chars]
        spaced = " ".join(names)
        tokens = spaced.split()
        if len(tokens) >= 2 and not any(t in SHORT_WORDS_RU for t in tokens):
            out.append(spaced)
        joined = "".join(n.replace(" ", "") for n in names)
        if len(joined) >= 4:
            out.append(joined)
    # «L» произносят и «эл», и «эль»: эс кью эль, эйч ти эм эль
    out += [re.sub(r"\bэл\b", "эль", v) for v in out if re.search(r"\bэл\b", v)]
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- источники
def fetch(cache: Path, name: str) -> Path:
    path = cache / name
    if not path.exists() or path.stat().st_size == 0:
        cache.mkdir(parents=True, exist_ok=True)
        print(f"  скачиваю {name} …", flush=True)
        req = urllib.request.Request(SOURCES[name], headers={"User-Agent": "aqua-linux-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            path.write_bytes(resp.read())
    return path


def load_freq(cache: Path) -> set[str]:
    words = set()
    for line in fetch(cache, "ru_50k.txt").read_text(encoding="utf-8").splitlines():
        w = line.split(" ")[0].strip().lower().replace("ё", "е")
        if w:
            words.add(w)
    for line in fetch(cache, "ru_100k.txt").read_text(encoding="utf-8").splitlines():
        w = line.strip().lower().replace("ё", "е")
        if w:
            words.add(w)
    return words


def load_cmu(cache: Path) -> dict[str, list[str]]:
    cmu = {}
    for line in fetch(cache, "cmudict.dict").read_text(encoding="latin-1").splitlines():
        parts = line.split("#")[0].split()
        if len(parts) < 2:
            continue
        word = re.sub(r"\(\d+\)$", "", parts[0])
        cmu.setdefault(word, parts[1:])
    return cmu


LATIN_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+#\-_'&!:/]*$")
CYR = re.compile(r"[А-Яа-яЁё]")


def load_wikidict(cache: Path):
    """(технические латинские названия, {английское название: кириллическое написание в ru-вики})."""
    dis = re.compile(r"^(?P<base>.+?) \((?P<d>[^)]+)\)$")
    latin_tech: set[str] = set()
    cyr_title: dict[str, str] = {}
    for line in fetch(cache, "en-ru_wiki.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        en, ru = parts
        ru_base = re.sub(r"\s*\([^)]*\)$", "", ru)
        m = dis.match(en)
        en_base = m.group("base") if m else en
        if m and m.group("d") in TECH_CATEGORIES and LATIN_TITLE.match(ru_base) and not CYR.search(ru_base):
            latin_tech.add(ru_base)
        if LATIN_TITLE.match(en_base) and CYR.search(ru_base) and not LATIN_TITLE.match(ru_base):
            if not m or m.group("d") in TECH_CATEGORIES:
                cyr_title.setdefault(en_base, ru_base)
    return latin_tech, cyr_title


def load_linguist(cache: Path) -> list[str]:
    names = []
    for line in fetch(cache, "languages.yml").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([^ #][^:]*):\s*$", line)
        if m:
            names.append(m.group(1).strip("\"'"))
    return names


def load_simple_icons(cache: Path) -> list[str]:
    data = json.loads(fetch(cache, "simple-icons.json").read_text(encoding="utf-8"))
    return [item["title"] for item in data if item.get("title")]


def load_curated(path: Path):
    entries = []      # (term, [variants], forced set, is_abbr)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        abbr = line.startswith("@")
        if abbr:
            line = line[1:].strip()
        parts = [p.strip() for p in line.split("|")]
        term, variants, forced = parts[0], [], set()
        for v in parts[1:]:
            if v.startswith("!"):
                v = v[1:]
                forced.add(norm(v))
            if v:
                variants.append(norm(v))
        if abbr:
            variants += letter_variants(term)
        entries.append((term, variants, forced, abbr))
    return entries


def norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- варианты для названия
def word_variants(word: str, cmu: dict) -> list[str]:
    w = word.lower()
    if not re.fullmatch(r"[a-z]+", w):
        return []
    out = []
    if len(w) <= 3 and w.isalpha() and (word.isupper() or len(w) <= 2):
        return letter_variants(w)
    if w in cmu:
        out += cmu_to_ru(cmu[w])
    for soft_g, u_as_a, y_as_ai in ((True, False, False), (False, True, True)):
        v = spell_to_ru(w, soft_g, u_as_a, y_as_ai)
        if v and v not in out:
            out.append(v)
    if word.isupper() and 2 <= len(w) <= 5:
        out += letter_variants(w)
    return out[:6]


def term_variants(term: str, cmu: dict) -> list[str]:
    words = [w for w in re.split(r"[\s\-_./]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", term)) if w]
    if not words or len(words) > 4 or any(re.search(r"[^A-Za-z]", w) for w in words):
        return []
    per_word = [word_variants(w, cmu) for w in words]
    if any(not v for v in per_word):
        return []
    out = []
    for combo in itertools.product(*per_word):
        out.append(" ".join(combo))
        if len(words) > 1:
            out.append("".join(combo))
        if len(out) >= 8:
            break
    return list(dict.fromkeys(out))


def similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- сборка
def build(cache: Path, out_path: Path) -> None:
    print("Источники:")
    freq = load_freq(cache)
    cmu = load_cmu(cache)
    latin_tech, cyr_title = load_wikidict(cache)
    linguist = load_linguist(cache)
    icons = load_simple_icons(cache)
    curated = load_curated(ROOT / "tools" / "terms_curated.txt")
    print(f"  частотный фильтр: {len(freq)} слов, CMUdict: {len(cmu)}, wikidict: {len(latin_tech)} тех. названий "
          f"латиницей, {len(cyr_title)} кириллицей; linguist: {len(linguist)}; simple-icons: {len(icons)}; "
          f"ручной список: {len(curated)}")

    variant_terms: dict[str, dict[str, int]] = defaultdict(dict)    # вариант → {термин: приоритет}
    forced_variants: set[str] = set()
    stats = defaultdict(int)

    def add(term: str, variant: str, prio: int, forced: bool = False) -> None:
        variant = norm(variant)
        if not variant or re.search(r"[a-z]", variant):
            return
        words = variant.split()
        if not forced:
            if len(words) == 1 and (len(variant) < 4 or variant in freq):
                return
            if len(words) > 1 and prio > 0 and all(w in freq for w in words):
                return
        else:
            forced_variants.add(variant)
        cur = variant_terms[variant].get(term)
        if cur is None or prio < cur:
            variant_terms[variant][term] = prio

    curated_terms = set()
    for term, variants, forced, abbr in curated:
        curated_terms.add(term.lower())
        for v in variants:
            add(term, v, 0, v in forced)
        stats["curated"] += 1

    def auto(term: str, prio: int, source: str) -> None:
        if term.lower() in curated_terms or term in NATIVE_CYRILLIC:
            return
        if term in cyr_title:
            return     # русская Википедия пишет это кириллицей («Твиттер», «Яндекс») — не трогаем
        variants = term_variants(term, cmu)
        if not variants:
            return
        stats[source] += 1
        for v in variants:
            add(term, v, prio)

    for t in icons:
        auto(t, 2, "simple-icons")
    for t in linguist:
        auto(t, 1, "linguist")
    for t in sorted(latin_tech):
        if len(t) >= 3 and re.search(r"[A-Za-z]{3}", t):
            auto(t, 3, "wikidict")

    # Варианты, которые подходят нескольким разным терминам, оставляем только за лучшим
    # (меньший приоритет); при равенстве — неоднозначный вариант отбрасываем.
    final: dict[str, str] = {}
    ambiguous = 0
    for variant, terms in variant_terms.items():
        best = min(terms.values())
        winners = [t for t, p in terms.items() if p == best]
        if len(winners) > 1 and best > 0:
            ambiguous += 1
            continue
        final[variant] = sorted(winners, key=len)[0]

    # Склонённые формы, совпадающие с обычными русскими словами, — в список «не трогать».
    blocked = set()
    for variant in final:
        last = variant.split()[-1]
        for form in declensions(last):
            if form in freq:
                blocked.add(form)
    # Формы, которые сами являются вариантами другого термина, не блокируем.
    blocked -= set(final)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    terms_count = len(set(final.values()))
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write("# Aqua Linux: встроенная база терминов (tools/build_terms.py). Источники: ручной список, "
                 "simple-icons (CC0), github-linguist (MIT), wikidict-ru/Wikidata (CC0), CMUdict (BSD).\n")
        for variant in sorted(final):
            fh.write(f"{variant}\t{final[variant]}\n")
        for form in sorted(blocked):
            fh.write(f"!{form}\n")
    print(f"Готово: {terms_count} терминов, {len(final)} вариантов, {len(blocked)} форм-исключений, "
          f"{ambiguous} неоднозначных вариантов отброшено → {out_path} ({out_path.stat().st_size // 1024} КБ)")
    print("  по источникам:", dict(stats))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "aqua-linux" / "termsdb"))
    ap.add_argument("--out", default=str(ROOT / "aqualinux" / "data" / "terms_ru.tsv.gz"))
    args = ap.parse_args()
    build(Path(args.cache), Path(args.out))


if __name__ == "__main__":
    main()
