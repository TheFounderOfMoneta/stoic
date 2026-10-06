"""Модель ваших интересов — линейный контекстный бандит (как LinUCB в рекомендациях новостей Yahoo).

Каждая показанная статья — наблюдение: её признаки (тема, подтема, герои, тип, длина, источник)
и итоговая ценность ваших действий (signals.py). Модель учит вклад каждого признака так, чтобы
сумма вкладов предсказывала ценность. Важное:

- Учитываются и показы без действий: статья, которую вы видели наверху и пропустили, — это
  наблюдение с ценностью около нуля. Поэтому тема, которую часто показывают и редко читают,
  честно опускается (а не только та, на которую жмут «не моя тема»).
- Признаки учатся совместно: если вы читаете разборы про ИИ, модель различает, что нравится —
  тема или тип материала, а не начисляет плюс всему сразу.
- Гребневая регрессия с притяжением к стартовым знаниям: заявленные темы и импорт из Google —
  это «априори», от которого модель отходит по мере накопления ваших действий.
- Две памяти: долгая (полураспад ~45 дней) и краткая (~4 дня, «на этой неделе увлёкся»).
  Краткая модель притягивается к долгой и перевешивает её, только когда свежих данных много.
- Неуверенность по каждому признаку: для ранжирования вклад берётся случайно в пределах
  неуверенности (выборка Томпсона) — малоизученные признаки иногда «выстреливают», так модель
  сама исследует новое.

Модель не хранит счётчики: каждый раз пересчитывается из сырого журнала действий, поэтому
формулу можно улучшать, не теряя накопленного.
"""
from __future__ import annotations

import math
import random
import time
import zlib
from dataclasses import dataclass, field

from ..util import day_key, jload, norm_entity
from .features import CONTENT_TYPES, SOURCE_TYPES, article_features, ftype
from .signals import ArticleSignals, collect_signals, exam_prob

LONG_HALF_LIFE_DAYS = 45.0
SHORT_HALF_LIFE_DAYS = 4.0
RIDGE = 2.0                # сила притяжения к априори (сколько «псевдонаблюдений»)
SHORT_RIDGE = 1.0          # краткая память легче отходит от долгой
LAM_MAX, LAM_K = 0.7, 2.0  # насколько краткая память может перевесить долгую
NOISE_SIGMA = 0.6          # разброс ценности одного наблюдения — для неуверенности Томпсона
TOPIC_PRIOR = 0.35         # стартовый вклад заявленной темы с весом 1
SWEEPS = 12                # проходов координатного спуска
CONFIDENCE_AT = 60         # после стольких осознанных действий персональная модель главная
SCORE_GAIN = 1.5           # перевод предсказанной ценности в шкалу 0–1 (для показа и порогов)


@dataclass
class FeatureStat:
    beta: float = 0.0       # итоговый вклад (смесь долгой и краткой памяти)
    beta_long: float = 0.0
    beta_short: float = 0.0
    prior: float = 0.0
    ev_long: float = 0.0    # сколько данных (взвешенно) про признак в долгой памяти
    ev_short: float = 0.0
    n_pos: int = 0          # сколько статей с признаком «зашли» — для объяснений
    n_neg: int = 0
    last_ts: float = 0.0

    @property
    def evidence(self) -> float:
        return self.ev_long

    @property
    def variance(self) -> float:
        return NOISE_SIGMA ** 2 / (RIDGE + self.ev_long)

    def mean(self) -> float:
        """0–1: насколько признак вам нравится (0,5 — нейтрально)."""
        return 1.0 / (1.0 + math.exp(-2.0 * self.beta))

    def sample_beta(self, rng: random.Random) -> float:
        return rng.gauss(self.beta, math.sqrt(self.variance))


@dataclass
class Personal:
    score: float                         # 0–1: насколько статья в вашем вкусе
    value: float = 0.0                   # предсказанная ценность (сумма вкладов)
    detail: list = field(default_factory=list)   # [(признак, вклад, 1)]
    novelty: float = 0.0                 # 0–1: насколько признаки статьи нам ещё незнакомы


@dataclass
class _Obs:
    feats: list
    target: float
    w_long: float
    w_short: float


class InterestModel:
    def __init__(self):
        self.stats: dict[str, FeatureStat] = {}
        self.signals: dict[int, ArticleSignals] = {}
        self.articles: dict[int, dict] = {}
        self.display: dict[str, str] = {}
        self.interactions = 0
        self.now = time.time()
        self.bias_content = 0.0
        self.bias_source = 0.0

    # ------------------------------------------------------------------ построение
    @classmethod
    def build(cls, storage, now: float | None = None, until: float | None = None,
              weights: dict | None = None) -> "InterestModel":
        m = cls()
        m.now = now if now is not None else time.time()
        if weights is None:
            weights = storage.active_weights()
        rows = storage.query("SELECT id, topic, subtopic, entities, domain, kind, words, bucket, title_ru, "
                             "title_orig, collected_at FROM articles")
        for r in rows:
            a = dict(r)
            a["entities"] = jload(a["entities"], [])
            m.articles[a["id"]] = a
            for e in a["entities"]:
                m.display.setdefault("entity:" + norm_entity(str(e)), str(e))
        words = {aid: int(a.get("words") or 0) for aid, a in m.articles.items()}
        m.signals = collect_signals(storage, until=until, weights=weights, words=words)
        m._apply_priors(storage)
        content_obs, source_obs = m._observations()
        m.bias_content = m._fit(content_obs)
        m.bias_source = m._fit(source_obs)
        m.interactions = sum(1 for s in m.signals.values()
                             if any(c.label != "пропуск" for c in s.contribs))
        return m

    def stat(self, feature: str) -> FeatureStat:
        st = self.stats.get(feature)
        if st is None:
            st = self.stats[feature] = FeatureStat()
        return st

    def _apply_priors(self, storage) -> None:
        for t in storage.topics(enabled_only=True):
            self.stat("topic:" + t["name"]).prior += TOPIC_PRIOR * float(t["weight"])
        for feature, (pos, neg) in storage.priors().items():
            # импорт (Google и т.п.) хранит «плюсы/минусы» — переводим в стартовый вклад
            self.stat(feature).prior += 0.15 * (pos - neg)

    def _observations(self) -> tuple[list[_Obs], list[_Obs]]:
        ln2 = math.log(2)
        content, source = [], []
        for aid, sig in self.signals.items():
            a = self.articles.get(aid)
            if not a or (not sig.contribs and not sig.shown):
                continue
            feats = article_features(a)
            cfeats = [f for f in feats if ftype(f) in CONTENT_TYPES]
            sfeats = [f for f in feats if ftype(f) in SOURCE_TYPES]
            engaged = any(c.label != "пропуск" for c in sig.contribs)
            # Вес наблюдения: вовлечённость — точно видел; только показ — с вероятностью, что разглядел.
            exposure = 1.0 if engaged else exam_prob(sig.min_position or 0)
            ts = max((c.ts for c in sig.contribs), default=sig.first_ts or self.now)
            age = max(0.0, (self.now - ts) / 86400.0)
            dl = math.exp(-ln2 * age / LONG_HALF_LIFE_DAYS)
            ds = math.exp(-ln2 * age / SHORT_HALF_LIFE_DAYS)
            yc = sum(c.amount for c in sig.contribs if c.scope & CONTENT_TYPES)
            ys = sum(c.amount for c in sig.contribs if c.scope & SOURCE_TYPES)
            if cfeats:
                content.append(_Obs(cfeats, yc, exposure * dl, exposure * ds))
            if sfeats:
                source.append(_Obs(sfeats, ys, exposure * dl, exposure * ds))
            for f in feats:
                st = self.stat(f)
                st.last_ts = max(st.last_ts, ts)
                if engaged and (yc >= 0.8 or sig.valuable):
                    st.n_pos += 1
                elif engaged and (yc <= -0.8 or sig.rejected):
                    st.n_neg += 1
        return content, source

    @staticmethod
    def _solve(obs: list[_Obs], weight_attr: str, prior_of, ridge: float = RIDGE) -> tuple[float, dict, dict]:
        """Гребневая регрессия координатным спуском: y ≈ смещение + Σ вкладов признаков."""
        beta: dict[str, float] = {}
        ev: dict[str, float] = {}
        index: dict[str, list[int]] = {}
        weights = [getattr(o, weight_attr) for o in obs]
        for i, o in enumerate(obs):
            for f in o.feats:
                if f not in beta:
                    beta[f] = prior_of(f)
                    ev[f] = 0.0
                ev[f] += weights[i]
                index.setdefault(f, []).append(i)
        total_w = sum(weights)
        if not obs or total_w <= 0:
            return 0.0, beta, ev
        bias = sum(w * o.target for w, o in zip(weights, obs)) / total_w
        pred = [bias + sum(beta[f] for f in o.feats) for o in obs]
        for _ in range(SWEEPS):
            # смещение (без регуляризации) — средняя ценность показа
            delta = sum(w * (o.target - p) for w, o, p in zip(weights, obs, pred)) / total_w
            if delta:
                bias += delta
                pred = [p + delta for p in pred]
            for f, idxs in index.items():
                num, den = ridge * prior_of(f), ridge
                for i in idxs:
                    w = weights[i]
                    num += w * (obs[i].target - pred[i] + beta[f])
                    den += w
                d = num / den - beta[f]
                if d:
                    beta[f] += d
                    for i in idxs:
                        pred[i] += d
        return bias, beta, ev

    def _fit(self, obs: list[_Obs]) -> float:
        def prior(f: str) -> float:
            return self.stats[f].prior if f in self.stats else 0.0
        bias_l, beta_l, ev_l = self._solve(obs, "w_long", prior)
        # краткая память притягивается к долгой: отклоняется, только если свежих данных много

        def long_of(f: str) -> float:
            return beta_l.get(f, prior(f))
        _bias_s, beta_s, ev_s = self._solve(obs, "w_short", long_of, ridge=SHORT_RIDGE)
        for f in beta_l:
            st = self.stat(f)
            st.beta_long = beta_l[f]
            st.beta_short = beta_s.get(f, st.beta_long)
            st.ev_long = ev_l.get(f, 0.0)
            st.ev_short = ev_s.get(f, 0.0)
            lam = LAM_MAX * st.ev_short / (st.ev_short + LAM_K)
            st.beta = (1 - lam) * st.beta_long + lam * st.beta_short
        for f, st in self.stats.items():
            if f not in beta_l and st.ev_long == 0:
                st.beta = st.beta_long = st.beta_short = st.prior
        return bias_l

    # ------------------------------------------------------------------ прогноз
    @property
    def confidence(self) -> float:
        """0–1: сколько мы о вас знаем. От неё зависит вес персональной части."""
        return min(1.0, self.interactions / CONFIDENCE_AT)

    def draw(self, rng: random.Random) -> "ThompsonDraw":
        """Одна выборка параметров на всю ленту: один признак — одно значение для всех статей."""
        return ThompsonDraw(self, rng)

    def personal(self, article: dict, draw: "ThompsonDraw | None" = None) -> Personal:
        feats = article_features(article)
        value, detail, topic_novelty, other_novelty = 0.0, [], None, []
        for f in feats:
            st = self.stats.get(f) or FeatureStat()
            b = draw(f) if draw is not None else st.beta
            value += b
            detail.append((f, b, 1.0))
            nov = 1.0 / (1.0 + st.ev_long + abs(st.prior) * RIDGE)
            if ftype(f) == "topic":
                topic_novelty = nov
            elif ftype(f) in ("sub", "entity"):
                other_novelty.append(nov)
        score = 1.0 / (1.0 + math.exp(-SCORE_GAIN * value))
        # Новизна — прежде всего про тему: знакомая тема с новым героем — уже не разведка.
        if topic_novelty is not None:
            novelty = topic_novelty
        else:
            novelty = sum(other_novelty) / len(other_novelty) if other_novelty else 1.0
        return Personal(score=score, value=value, detail=detail, novelty=novelty)

    # ------------------------------------------------------------------ сводки
    def top_features(self, positive: bool = True, limit: int = 12, min_evidence: float = 1.0,
                     types: frozenset | None = None) -> list[tuple[str, float, float]]:
        """Самые любимые (или нелюбимые) признаки: (признак, 0–1, сколько данных)."""
        items = [(f, st.mean(), st.ev_long) for f, st in self.stats.items()
                 if (types is None or ftype(f) in types) and st.ev_long >= min_evidence]
        items.sort(key=lambda x: x[1], reverse=positive)
        items = [x for x in items if (x[1] > 0.55 if positive else x[1] < 0.45)]
        return items[:limit]


class ThompsonDraw:
    def __init__(self, model: InterestModel, rng: random.Random):
        self.model = model
        self.rng = rng
        self.cache: dict[str, float] = {}

    def __call__(self, feature: str) -> float:
        if feature not in self.cache:
            st = self.model.stats.get(feature) or FeatureStat()
            self.cache[feature] = st.sample_beta(self.rng)
        return self.cache[feature]


def day_rng(salt: str = "", now: float | None = None) -> random.Random:
    """Генератор случайности на день: лента не «прыгает» при каждом обновлении в течение дня."""
    key = f"{day_key(now if now is not None else time.time())}:{salt}"
    return random.Random(zlib.crc32(key.encode("utf-8")))
