"""Сигналы: как ваши действия превращаются в «ценность» статьи.

По принципам больших систем:
- время чтения важнее клика (YouTube взвешивает по времени просмотра — против кликбейта);
- время считается относительно длины текста (Yahoo: «время на странице» с поправкой на длину);
- осознанный негатив весит намного больше пассивного позитива (X: лайк 0,5, «не интересно» −74);
- пропуск статьи наверху ленты значит больше, чем пропуск внизу (поправка на позицию, YouTube 2019);
- отдельно от «читал» — «было ли полезно» (опросы, как «ценный просмотр» YouTube).

Каждое действие даёт вклад с «адресом»: на что он влияет — на содержание (тема, герои, тип,
длина), на источник или на всё сразу. «Уже знал» — претензия к свежести источника, а не к теме;
«не моя тема» — к содержанию, а не к сайту.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..util import day_key
from .features import ALL_TYPES, CONTENT_TYPES, SOURCE_TYPES

READ_WPM = 200            # слов в минуту — ожидаемая скорость чтения
MAX_EXPECTED_WORDS = 1200  # дальше ~1000 слов время чтения почти не растёт (Yahoo)

DEFAULT_WEIGHTS: dict[str, float] = {
    "impression_skip": -0.05,     # показана, но ничего не сделали (с поправкой на позицию)
    "expand": 0.20,               # раскрыли «Коротко» в ленте
    "skim": 0.15,                 # открыли и пробежали глазами
    "short_click_source": -0.50,  # открыли и сразу ушли — заголовок обманул (источнику)
    "short_click_content": -0.15,
    "read": 1.00,                 # дочитали (≥50% ожидаемого времени и ≥70% прокрутки)
    "revisit": 0.50,              # вернулись к статье в другой день
    "like": 1.00,
    "superlike": 2.00,
    "save": 1.50,
    "follow": 1.50,               # следить за сюжетом
    "ask": 2.50,                  # вопрос Claude или заметка — самое дорогое действие
    "known": -1.00,               # «уже знал» — несвежесть источника
    "shallow": -1.00,             # «поверхностно» — источнику и типу материала
    "dislike": -3.00,             # «не моя тема»
    "clickbait_source": -5.00,
    "clickbait_content": -0.50,
    "survey_scale": 0.80,         # ★5 → +1,6; ★1 → −1,6
}

# Реакции-переключатели: последнее событие (1 — включено, 0 — снято) определяет состояние.
TOGGLES = ("like", "superlike", "dislike", "known", "shallow", "clickbait", "save", "follow")
# Подсказки, которые калибруются по опросам (неявные сигналы).
IMPLICIT = ("expand", "skim", "read", "revisit", "short_click_source", "impression_skip")

KIND_SCOPE = frozenset({"kind"})


@dataclass
class Contribution:
    ts: float
    amount: float
    scope: frozenset
    label: str


@dataclass
class ArticleSignals:
    article_id: int
    contribs: list[Contribution] = field(default_factory=list)
    implicit: dict = field(default_factory=dict)      # для калибровки весов по опросам
    reactions: dict = field(default_factory=dict)     # итоговое состояние переключателей
    stars: int | None = None
    min_position: int | None = None
    shown: bool = False
    first_ts: float = 0.0

    @property
    def value(self) -> float:
        """Итоговая ценность для содержания статьи (тема, герои, тип)."""
        return sum(c.amount for c in self.contribs if c.scope & CONTENT_TYPES)

    @property
    def valuable(self) -> bool:
        r = self.reactions
        if self.stars is not None:
            return self.stars >= 4
        return bool(r.get("like") or r.get("superlike") or r.get("save") or r.get("follow")
                    or self.implicit.get("ask") or self.implicit.get("read_score", 0) >= 0.6)

    @property
    def rejected(self) -> bool:
        r = self.reactions
        if self.stars is not None and self.stars <= 2:
            return True
        return bool(r.get("dislike") or r.get("clickbait") or r.get("known") or r.get("shallow")
                    or self.implicit.get("short_click"))


def exam_prob(position: int) -> float:
    """Вероятность, что статью вообще разглядели: верх ленты видят почти всегда, низ — реже."""
    return 1.0 / (1.0 + max(0, position) / 4.0)


def expected_ms(words: int) -> int:
    words = max(80, min(int(words or 0), MAX_EXPECTED_WORDS))
    return int(words / READ_WPM * 60_000)


def merge_weights(custom: dict | None) -> dict:
    w = dict(DEFAULT_WEIGHTS)
    for k, v in (custom or {}).items():
        if k in w and isinstance(v, (int, float)):
            w[k] = float(v)
    return w


def collect_signals(storage, until: float | None = None, weights: dict | None = None,
                    words: dict[int, int] | None = None) -> dict[int, ArticleSignals]:
    """Все сигналы по всем статьям (до момента until — для честной проверки на прошлом)."""
    w = merge_weights(weights)
    cond, params = ("WHERE ts < ?", (until,)) if until is not None else ("", ())
    out: dict[int, ArticleSignals] = {}

    def get(aid: int) -> ArticleSignals:
        if aid not in out:
            out[aid] = ArticleSignals(aid)
        return out[aid]

    # --- показы ---------------------------------------------------------------
    shown_days: dict[int, set] = {}
    last_imp: dict[int, float] = {}
    for r in storage.query(f"SELECT article_id, ts, position, visible_ms FROM impressions {cond}", params):
        s = get(r["article_id"])
        if r["visible_ms"] >= 1000:
            s.shown = True
            shown_days.setdefault(s.article_id, set()).add(day_key(r["ts"]))
            last_imp[s.article_id] = max(last_imp.get(s.article_id, 0.0), r["ts"])
            s.min_position = r["position"] if s.min_position is None else min(s.min_position, r["position"])
        s.first_ts = r["ts"] if not s.first_ts else min(s.first_ts, r["ts"])

    # --- события ---------------------------------------------------------------
    toggles: dict[int, dict[str, tuple[float, float]]] = {}
    expand_ts: dict[int, float] = {}
    ask_ts: dict[int, float] = {}
    open_ts: dict[int, float] = {}
    cond_e = cond + (" AND" if cond else "WHERE") + " article_id IS NOT NULL"
    for r in storage.query(f"SELECT article_id, ts, kind, value FROM events {cond_e} ORDER BY ts, id", params):
        aid, kind = r["article_id"], r["kind"]
        get(aid)
        if kind in TOGGLES:
            toggles.setdefault(aid, {})[kind] = (float(r["value"]), r["ts"])
        elif kind == "expand":
            expand_ts.setdefault(aid, r["ts"])
        elif kind in ("ask", "note"):
            ask_ts.setdefault(aid, r["ts"])
        elif kind == "open":
            open_ts.setdefault(aid, r["ts"])

    # --- чтение ---------------------------------------------------------------
    reads: dict[int, dict] = {}
    for r in storage.query(f"SELECT article_id, ts, active_ms, scroll_pct, expected_ms FROM reads {cond}", params):
        d = reads.setdefault(r["article_id"], {"active": 0, "scroll": 0.0, "expected": 0, "days": set(), "ts": 0.0})
        d["active"] += max(0, int(r["active_ms"]))
        d["scroll"] = max(d["scroll"], float(r["scroll_pct"]))
        d["expected"] = max(d["expected"], int(r["expected_ms"]))
        if r["active_ms"] >= 5000:
            d["days"].add(day_key(r["ts"]))
        d["ts"] = max(d["ts"], r["ts"])
        get(r["article_id"])

    # --- опросы ---------------------------------------------------------------
    stars: dict[int, tuple[int, float]] = {}
    for r in storage.query(f"SELECT article_id, ts, stars FROM surveys {cond} ORDER BY ts, id", params):
        stars[r["article_id"]] = (int(r["stars"]), r["ts"])
        get(r["article_id"])

    for aid, s in out.items():
        add = s.contribs.append
        state = {k: v for k, (v, _ts) in toggles.get(aid, {}).items() if v > 0}
        s.reactions = {k: True for k in state}
        tts = {k: ts for k, (_v, ts) in toggles.get(aid, {}).items()}
        positive = bool(expand_ts.get(aid) or ask_ts.get(aid) or aid in reads or aid in open_ts
                        or state.keys() & {"like", "superlike", "save", "follow"})
        negative = bool(state.keys() & {"dislike", "known", "shallow", "clickbait"})

        # Пропуск: показана, но ничего не сделали. Чем выше позиция — тем сильнее сигнал.
        if s.shown and not positive and not negative:
            days = min(3, len(shown_days.get(aid, ())))
            add(Contribution(last_imp[aid], w["impression_skip"] * exam_prob(s.min_position or 0) * days,
                             ALL_TYPES, "пропуск"))
            s.implicit["skipped"] = 1
        if aid in expand_ts:
            add(Contribution(expand_ts[aid], w["expand"], ALL_TYPES, "раскрыл"))
            s.implicit["expand"] = 1
        d = reads.get(aid)
        if d and d["active"] > 0:
            exp = d["expected"] or expected_ms((words or {}).get(aid, 0))
            ratio = d["active"] / max(1, exp)
            s.implicit["opened"] = 1
            if ratio < 0.15 and d["scroll"] < 0.3:
                if d["active"] < 8000:
                    add(Contribution(d["ts"], w["short_click_source"], SOURCE_TYPES, "короткий клик"))
                    add(Contribution(d["ts"], w["short_click_content"], CONTENT_TYPES, "короткий клик"))
                    s.implicit["short_click"] = 1
                else:
                    add(Contribution(d["ts"], w["skim"], ALL_TYPES, "пробежал"))
                    s.implicit["skim"] = 1
            else:
                rs = min(1.0, ratio / 0.5) * min(1.0, d["scroll"] / 0.7)
                s.implicit["read_score"] = round(rs, 3)
                if rs > 0.1:
                    add(Contribution(d["ts"], w["read"] * rs, ALL_TYPES, "дочитал" if rs >= 0.6 else "читал"))
                else:
                    add(Contribution(d["ts"], w["skim"], ALL_TYPES, "пробежал"))
                    s.implicit["skim"] = 1
            if len(d["days"]) >= 2:
                add(Contribution(d["ts"], w["revisit"], ALL_TYPES, "вернулся"))
                s.implicit["revisit"] = 1
        if aid in ask_ts:
            add(Contribution(ask_ts[aid], w["ask"], ALL_TYPES, "спросил Claude"))
            s.implicit["ask"] = 1
        for kind in state:
            ts = tts[kind]
            if kind in ("like", "superlike", "save"):
                add(Contribution(ts, w[kind], ALL_TYPES, kind))
            elif kind == "follow":
                add(Contribution(ts, w["follow"], CONTENT_TYPES, "следить"))
            elif kind == "dislike":
                add(Contribution(ts, w["dislike"], CONTENT_TYPES, "не моя тема"))
            elif kind == "known":
                add(Contribution(ts, w["known"], SOURCE_TYPES, "уже знал"))
            elif kind == "shallow":
                add(Contribution(ts, w["shallow"], SOURCE_TYPES | KIND_SCOPE, "поверхностно"))
            elif kind == "clickbait":
                add(Contribution(ts, w["clickbait_source"], SOURCE_TYPES, "кликбейт"))
                add(Contribution(ts, w["clickbait_content"], CONTENT_TYPES, "кликбейт"))
        if aid in stars:
            st, ts = stars[aid]
            s.stars = st
            add(Contribution(ts, (st - 3) * w["survey_scale"], ALL_TYPES, f"опрос ★{st}"))
    return out
