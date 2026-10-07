"""Доска: схема рисуется от руки, а Claude получает её структурой — как в Mermaid.

Каждый штрих сразу получает смысл и привязку:
- замкнутый контур → фигура: прямоугольник, овал, круг или ромб (по тому, как нарисовано);
  если внутри фигуры есть другие фигуры — это группа (subgraph);
- линия от фигуры к фигуре → стрелка, привязанная к обеим: двигаете фигуру — стрелка тянется
  за ней и сохраняет ваш росчерк. Стрелка идёт туда, куда вели линию;
- «галочка» у конца стрелки (или наконечник тем же росчерком) → наконечник; у начала — стрелка
  в обратную сторону, у обоих концов — в обе стороны;
- штрихи внутри фигуры → её подпись от руки; рядом с серединой стрелки → подпись стрелки;
- написали слово, потом обвели — слово становится подписью новой фигуры;
- стрелка, которая никуда не попала, ждёт: нарисуйте фигуру вокруг её конца — привяжется.
Подписи можно и напечатать. Всё, что не легло в структуру, остаётся рисунком от руки: его Claude
увидит на картинке доски.

Модель — без Qt: её проверяют тесты, а окно только рисует и передаёт штрихи.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field

from .util import plural

Pt = tuple[float, float]

SNAP = 18.0                 # насколько рядом с фигурой можно начать или закончить стрелку
SHAPE_KINDS = ("rect", "round", "ellipse", "circle", "diamond")
KIND_NAMES = {"rect": "прямоугольник", "round": "скруглённый", "ellipse": "овал", "circle": "круг",
              "diamond": "ромб"}


# ================================================================ геометрия
def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def path_len(pts: list[Pt]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def bbox(pts: list[Pt]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    if ll == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll))
    return dist(p, (a[0] + t * dx, a[1] + t * dy))


def poly_dist(p: Pt, pts: list[Pt]) -> float:
    if len(pts) == 1:
        return dist(p, pts[0])
    return min(seg_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def hull(pts: list[Pt]) -> list[Pt]:
    """Выпуклая оболочка (монотонная цепь)."""
    ps = sorted(set(pts))
    if len(ps) < 3:
        return ps

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in ps:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(ps):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def area(poly: list[Pt]) -> float:
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def simplify(pts: list[Pt], eps: float = 0.7) -> list[Pt]:
    """Рамер — Дуглас — Пекер: меньше точек, тот же росчерк."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, -1
        for k in range(i + 1, j):
            d = seg_dist(pts[k], pts[i], pts[j])
            if d > best:
                best, idx = d, k
        if best > eps and idx > 0:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


def smooth(pts: list[Pt]) -> list[Pt]:
    """Лёгкое сглаживание дрожания мыши; концы на месте."""
    if len(pts) < 4:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        out.append(((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4))
    out.append(pts[-1])
    return out


def point_at(pts: list[Pt], frac: float) -> Pt:
    """Точка на ломаной на доле frac её длины."""
    total = path_len(pts)
    if total == 0 or len(pts) == 1:
        return pts[0]
    target = total * max(0.0, min(1.0, frac))
    run = 0.0
    for i in range(len(pts) - 1):
        d = dist(pts[i], pts[i + 1])
        if run + d >= target and d > 0:
            t = (target - run) / d
            return (pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1]))
        run += d
    return pts[-1]


def _diag(pts: list[Pt]) -> float:
    x0, y0, x1, y1 = bbox(pts)
    return math.hypot(x1 - x0, y1 - y0)


def _r(p: Pt) -> list[float]:
    return [round(p[0], 1), round(p[1], 1)]


# ================================================================ распознавание
def is_closed(pts: list[Pt]) -> bool:
    """Замкнутый ли контур: конец вернулся к началу (с недолётом или перелётом)."""
    if len(pts) < 5:
        return False
    x0, y0, x1, y1 = bbox(pts)
    w, h = x1 - x0, y1 - y0
    length = path_len(pts)
    if length < 60 or min(w, h) < 14:
        return False
    if dist(pts[0], pts[-1]) <= max(16.0, 0.12 * length):
        return True
    # перелёт: конец заехал на начало контура, или начало — на конец
    n = max(2, len(pts) // 4)
    tol = max(14.0, 0.07 * length)
    return poly_dist(pts[-1], pts[:n]) <= tol or poly_dist(pts[0], pts[-n:]) <= tol


def min_rect(pts: list[Pt]) -> tuple[float, float]:
    """Наименьшая описанная рамка под любым углом: (её площадь, наклон к осям в градусах, 0–45)."""
    hp = hull(pts)
    if len(hp) < 3:
        return 0.0, 0.0
    best, angle = math.inf, 0.0
    for i in range(len(hp)):
        (x1, y1), (x2, y2) = hp[i], hp[(i + 1) % len(hp)]
        t = math.atan2(y2 - y1, x2 - x1)
        c, s = math.cos(-t), math.sin(-t)
        xs = [p[0] * c - p[1] * s for p in hp]
        ys = [p[0] * s + p[1] * c for p in hp]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if a < best:
            best, angle = a, math.degrees(t) % 90
    return best, min(angle, 90 - angle)


def classify_closed(pts: list[Pt]) -> str:
    """Какая фигура. Заполненность выпуклой оболочкой: рамки по осям (у ромба ~0,5) и наименьшей
    рамки под любым углом (у прямоугольника ~1 даже с наклоном, у овала ~0,79)."""
    x0, y0, x1, y1 = bbox(pts)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    ha = area(hull(pts))
    fa = ha / (w * h)
    mr, tilt = min_rect(pts)
    fm = ha / mr if mr else fa
    if fm >= 0.86 and tilt <= 20:
        kind = "rect"
    elif fa < 0.64 or fm >= 0.86:            # ромб по осям или квадрат, повёрнутый на угол
        kind = "diamond"
    elif fa >= 0.88:
        kind = "rect"
    else:
        kind = "ellipse"
    if kind == "ellipse" and 0.8 <= w / h <= 1.25:
        kind = "circle"
    return kind


def split_head(pts: list[Pt]) -> tuple[list[Pt], bool]:
    """Стрелка с наконечником одним росчерком: линия до острия и «галочка» обратно. Отрезаем галочку."""
    if len(pts) < 4:
        return pts, False
    start = pts[0]
    k = max(range(len(pts)), key=lambda i: dist(start, pts[i]))
    if k >= len(pts) - 1 or k < 2:
        return pts, False
    tail = pts[k:]
    tail_len = path_len(tail)
    total = path_len(pts)
    tip = pts[k]
    back = max(dist(tip, p) for p in tail)
    if 6 <= tail_len <= 70 and tail_len <= 0.4 * total and back <= 40 and dist(start, tip) >= 30:
        return pts[:k + 1], True
    return pts, False


# ================================================================ модель
@dataclass
class Shape:
    id: int
    kind: str
    x: float
    y: float
    w: float
    h: float
    ink: list[list[Pt]] = field(default_factory=list)     # контур от руки — относительно (x, y)
    hand: list[list[Pt]] = field(default_factory=list)    # подпись от руки — относительно (x, y)
    label: str = ""

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(1.0, self.w * self.h)

    def contains(self, p: Pt, pad: float = 0.0) -> bool:
        rx, ry = self.w / 2 + pad, self.h / 2 + pad
        if rx <= 0 or ry <= 0:
            return False
        dx, dy = abs(p[0] - self.cx), abs(p[1] - self.cy)
        if self.kind in ("ellipse", "circle"):
            return (dx / rx) ** 2 + (dy / ry) ** 2 <= 1.0
        if self.kind == "diamond":
            return dx / rx + dy / ry <= 1.0
        return dx <= rx and dy <= ry

    def border_point(self, toward: Pt) -> Pt:
        """Точка на краю фигуры по направлению от центра к toward."""
        dx, dy = toward[0] - self.cx, toward[1] - self.cy
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return (self.cx, self.y)
        rx, ry = max(1.0, self.w / 2), max(1.0, self.h / 2)
        if self.kind in ("ellipse", "circle"):
            t = 1 / math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2)
        elif self.kind == "diamond":
            t = 1 / (abs(dx) / rx + abs(dy) / ry)
        else:
            t = min(rx / abs(dx) if dx else math.inf, ry / abs(dy) if dy else math.inf)
        return (self.cx + t * dx, self.cy + t * dy)

    def outline(self) -> list[list[Pt]]:
        """Контур в координатах доски: росчерк от руки или ровная фигура."""
        if self.ink:
            return [[(self.x + p[0], self.y + p[1]) for p in s] for s in self.ink]
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.kind in ("ellipse", "circle"):
            return [[(x + w / 2 + w / 2 * math.cos(a * math.pi / 18), y + h / 2 + h / 2 * math.sin(a * math.pi / 18))
                     for a in range(37)]]
        if self.kind == "diamond":
            return [[(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2), (x + w / 2, y)]]
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]

    def hand_abs(self) -> list[list[Pt]]:
        return [[(self.x + p[0], self.y + p[1]) for p in s] for s in self.hand]


@dataclass
class Edge:
    id: int
    a: int | None = None              # фигура начала (None — конец свободен)
    b: int | None = None
    ua: Pt | None = None              # где на фигуре начало: доли ширины и высоты (None — по центрам)
    ub: Pt | None = None
    pa: Pt = (0.0, 0.0)               # свободный конец — точка на доске
    pb: Pt = (0.0, 0.0)
    ink: list[Pt] = field(default_factory=list)   # росчерк как нарисован (доска) — тянется за концами
    head_a: bool = False
    head_b: bool = True
    explicit_b: bool = False          # наконечник у конца нарисован явно
    style: str = "solid"              # solid | dotted | thick
    label: str = ""
    hand: list[list[Pt]] = field(default_factory=list)  # подпись от руки — относительно середины стрелки


@dataclass
class Note:
    id: int
    x: float
    y: float
    text: str


@dataclass
class Ink:
    id: int
    pts: list[Pt]


class Board:
    """Всё, что на доске, и правила привязки."""

    def __init__(self):
        self.shapes: dict[int, Shape] = {}
        self.edges: dict[int, Edge] = {}
        self.notes: dict[int, Note] = {}
        self.ink: dict[int, Ink] = {}
        self.next_id = 1

    def _id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    # ------------------------------------------------------------- хранение
    def to_dict(self) -> dict:
        def strokes(ss):
            return [[_r(p) for p in s] for s in ss]
        return {
            "v": 1, "next_id": self.next_id,
            "shapes": [{"id": s.id, "kind": s.kind, "x": round(s.x, 1), "y": round(s.y, 1), "w": round(s.w, 1),
                        "h": round(s.h, 1), "ink": strokes(s.ink), "hand": strokes(s.hand), "label": s.label}
                       for s in self.shapes.values()],
            "edges": [{"id": e.id, "a": e.a, "b": e.b, "ua": list(e.ua) if e.ua else None,
                       "ub": list(e.ub) if e.ub else None, "pa": _r(e.pa), "pb": _r(e.pb),
                       "ink": [_r(p) for p in e.ink], "head_a": e.head_a, "head_b": e.head_b,
                       "explicit_b": e.explicit_b, "style": e.style, "label": e.label, "hand": strokes(e.hand)}
                      for e in self.edges.values()],
            "notes": [{"id": n.id, "x": round(n.x, 1), "y": round(n.y, 1), "text": n.text} for n in self.notes.values()],
            "ink": [{"id": k.id, "pts": [_r(p) for p in k.pts]} for k in self.ink.values()],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Board":
        b = cls()
        if not isinstance(d, dict):
            return b

        def strokes(ss):
            return [[(float(p[0]), float(p[1])) for p in s] for s in ss or [] if s]
        try:
            for s in d.get("shapes") or []:
                kind = s.get("kind") if s.get("kind") in SHAPE_KINDS else "rect"
                b.shapes[int(s["id"])] = Shape(int(s["id"]), kind, float(s["x"]), float(s["y"]),
                                               max(4.0, float(s["w"])), max(4.0, float(s["h"])),
                                               strokes(s.get("ink")), strokes(s.get("hand")), str(s.get("label", "")))
            for e in d.get("edges") or []:
                b.edges[int(e["id"])] = Edge(
                    int(e["id"]), e.get("a"), e.get("b"), tuple(e["ua"]) if e.get("ua") else None,
                    tuple(e["ub"]) if e.get("ub") else None, tuple(e.get("pa") or (0, 0)), tuple(e.get("pb") or (0, 0)),
                    [(float(p[0]), float(p[1])) for p in e.get("ink") or []], bool(e.get("head_a")),
                    bool(e.get("head_b", True)), bool(e.get("explicit_b")), e.get("style") or "solid",
                    str(e.get("label", "")), strokes(e.get("hand")))
            for n in d.get("notes") or []:
                b.notes[int(n["id"])] = Note(int(n["id"]), float(n["x"]), float(n["y"]), str(n.get("text", "")))
            for k in d.get("ink") or []:
                pts = [(float(p[0]), float(p[1])) for p in k.get("pts") or []]
                if pts:
                    b.ink[int(k["id"])] = Ink(int(k["id"]), pts)
        except (KeyError, TypeError, ValueError, IndexError):
            return cls()
        for e in b.edges.values():                    # связь с исчезнувшей фигурой — свободный конец
            if e.a is not None and e.a not in b.shapes:
                e.a, e.ua = None, None
            if e.b is not None and e.b not in b.shapes:
                e.b, e.ub = None, None
        ids = [*b.shapes, *b.edges, *b.notes, *b.ink]
        b.next_id = max([int(d.get("next_id") or 1), *(i + 1 for i in ids)])
        return b

    def copy(self) -> "Board":
        return copy.deepcopy(self)

    def is_empty(self) -> bool:
        return not (self.shapes or self.edges or self.notes or self.ink)

    # ------------------------------------------------------------- вопросы
    def parent_map(self) -> dict[int, int | None]:
        """Для каждой фигуры — наименьшая фигура, внутри которой она лежит (её группа)."""
        res: dict[int, int | None] = {}
        shapes = list(self.shapes.values())
        for s in shapes:
            best = None
            for o in shapes:
                if o.id != s.id and o.area > s.area * 1.3 and o.contains((s.cx, s.cy)):
                    if best is None or o.area < best.area:
                        best = o
            res[s.id] = best.id if best else None
        return res

    def parent(self, s: Shape) -> Shape | None:
        pid = self.parent_map().get(s.id)
        return self.shapes.get(pid) if pid is not None else None

    def children(self, s: Shape, pm: dict | None = None) -> list[Shape]:
        pm = pm if pm is not None else self.parent_map()
        return [self.shapes[i] for i, p in pm.items() if p == s.id]

    def descendants(self, s: Shape) -> list[Shape]:
        pm = self.parent_map()
        out, stack = [], [s]
        while stack:
            for c in self.children(stack.pop(), pm):
                out.append(c)
                stack.append(c)
        return out

    def group_ids(self, pm: dict | None = None) -> set[int]:
        pm = pm if pm is not None else self.parent_map()
        return {p for p in pm.values() if p is not None}

    def is_group(self, s: Shape) -> bool:
        return s.id in self.group_ids()

    def shape_at(self, p: Pt, snap: float = 0.0) -> Shape | None:
        """Самая маленькая фигура под точкой; если ни одной — ближайшая в пределах snap."""
        inside = [s for s in self.shapes.values() if s.contains(p)]
        if inside:
            return min(inside, key=lambda s: s.area)
        if snap:
            near = [s for s in self.shapes.values() if s.contains(p, snap)]
            if near:
                return min(near, key=lambda s: dist(p, s.border_point(p)))
        return None

    def endpoint(self, e: Edge, which: str) -> Pt:
        sid, u, free = (e.a, e.ua, e.pa) if which == "a" else (e.b, e.ub, e.pb)
        s = self.shapes.get(sid) if sid is not None else None
        if s is None:
            return free
        if u is not None:
            return (s.x + u[0] * s.w, s.y + u[1] * s.h)
        other_id, other_free = (e.b, e.pb) if which == "a" else (e.a, e.pa)
        other = self.shapes.get(other_id) if other_id is not None else None
        target = (other.cx, other.cy) if other is not None else other_free
        if other is not None and other.id == s.id:
            target = (s.cx + s.w, s.cy - s.h)
        return s.border_point(target)

    def edge_path(self, e: Edge) -> list[Pt]:
        """Росчерк стрелки, натянутый между её текущими концами (поворот и растяжение)."""
        a, b = self.endpoint(e, "a"), self.endpoint(e, "b")
        if len(e.ink) < 2:
            return [a, b]
        i0, i1 = complex(*e.ink[0]), complex(*e.ink[-1])
        if abs(i1 - i0) < 1e-6:
            return [a, b]
        za, zb = complex(*a), complex(*b)
        k = (zb - za) / (i1 - i0)
        out = []
        for p in e.ink:
            z = za + (complex(*p) - i0) * k
            out.append((z.real, z.imag))
        return out

    def edge_mid(self, e: Edge) -> Pt:
        return point_at(self.edge_path(e), 0.5)

    def edge_hand_abs(self, e: Edge) -> list[list[Pt]]:
        m = self.edge_mid(e)
        return [[(m[0] + p[0], m[1] + p[1]) for p in s] for s in e.hand]

    def bounds(self) -> tuple[float, float, float, float] | None:
        pts: list[Pt] = []
        for s in self.shapes.values():
            pts += [(s.x, s.y), (s.x + s.w, s.y + s.h)]
        for e in self.edges.values():
            pts += self.edge_path(e)
            for st in self.edge_hand_abs(e):
                pts += st
        for n in self.notes.values():
            pts += [(n.x, n.y), (n.x + max(30, 7.5 * len(n.text)), n.y + 20)]
        for k in self.ink.values():
            pts += k.pts
        return bbox(pts) if pts else None

    def stats(self) -> dict:
        gids = self.group_ids()
        groups = [s for s in self.shapes.values() if s.id in gids]
        nodes = [s for s in self.shapes.values() if s.id not in gids]
        bound = [e for e in self.edges.values() if e.a is not None and e.b is not None]
        hand = sum(len(s.hand) for s in self.shapes.values()) + sum(len(e.hand) for e in self.edges.values())
        return {"nodes": len(nodes), "groups": len(groups), "edges": len(bound),
                "dangling": len(self.edges) - len(bound), "hand": hand, "ink": len(self.ink),
                "notes": len(self.notes),
                "unnamed": sum(1 for s in nodes if not s.label.strip() and not s.hand)}

    def needs_image(self) -> bool:
        """Есть то, что словами не передать: подписи и рисунки от руки."""
        st = self.stats()
        return bool(st["hand"] or st["ink"])

    # ------------------------------------------------------------- штрих → смысл
    def add_stroke(self, raw: list[Pt]) -> dict:
        """Главное: понять, чем стал штрих, и привязать его. Возвращает {'kind': …, 'id': …}."""
        pts = simplify(smooth([(float(x), float(y)) for x, y in raw]), 0.6)
        if len(pts) < 2:
            pts = [pts[0], (pts[0][0] + 0.5, pts[0][1] + 0.5)] if pts else []
        if not pts:
            return {"kind": "none"}
        head = self._as_arrowhead(pts)
        if head:
            return head
        if is_closed(pts):
            return self._add_closed(pts)
        return self._add_open(pts)

    def _as_arrowhead(self, pts: list[Pt]) -> dict | None:
        x0, y0, x1, y1 = bbox(pts)
        if max(x1 - x0, y1 - y0) > 40 or path_len(pts) > 110 or not self.edges or is_closed(pts):
            return None
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        best, which, edge = 1e9, "", None
        for e in self.edges.values():
            for w in ("a", "b"):
                end = self.endpoint(e, w)
                d = min(dist(end, p) for p in pts)
                if d <= 12 and dist(c, end) <= 26 and d < best:
                    best, which, edge = d, w, e
        if edge is None:
            return None
        # наконечник лежит снаружи фигуры, к которой пришла стрелка; штрих внутри неё — это подпись
        sid = edge.b if which == "b" else edge.a
        s = self.shapes.get(sid) if sid is not None else None
        if s is not None and sum(1 for p in pts if s.contains(p)) > 0.5 * len(pts):
            return None
        if which == "b":
            edge.head_b, edge.explicit_b = True, True
        else:
            edge.head_a = True
            if not edge.explicit_b:
                edge.head_b = False
        return {"kind": "arrowhead", "id": edge.id}

    def _host(self, pts: list[Pt], pad: float = 3.0) -> Shape | None:
        """Наименьшая фигура, в которой лежит весь штрих."""
        hosts = [s for s in self.shapes.values() if all(s.contains(p, pad) for p in pts)]
        return min(hosts, key=lambda s: s.area) if hosts else None

    def _add_closed(self, pts: list[Pt]) -> dict:
        x0, y0, x1, y1 = bbox(pts)
        w, h = x1 - x0, y1 - y0
        host = self._host(pts, pad=4)
        if host is not None:
            if w * h >= 0.55 * host.area:              # обвели ещё раз ту же фигуру
                host.ink.append([(p[0] - host.x, p[1] - host.y) for p in pts])
                return {"kind": "retrace", "id": host.id}
            if min(w, h) < 28:                          # маленький кружок внутри — буква подписи
                return self._hand(host, pts)
        s = Shape(self._id(), classify_closed(pts), x0, y0, max(w, 4.0), max(h, 4.0),
                  [[(p[0] - x0, p[1] - y0) for p in pts]])
        self.shapes[s.id] = s
        # написали слово, потом обвели: штрихи и надписи внутри становятся подписью фигуры
        for k in list(self.ink.values()):
            if all(s.contains(p, 2) for p in k.pts) and self.shape_at(k.pts[0]) is s:
                s.hand.append([(p[0] - s.x, p[1] - s.y) for p in k.pts])
                del self.ink[k.id]
        for n in list(self.notes.values()):
            if s.contains((n.x + 4, n.y + 9)) and self.shape_at((n.x + 4, n.y + 9)) is s:
                s.label = (s.label + " " + n.text).strip()
                del self.notes[n.id]
        self.bind_free_ends()
        return {"kind": "shape", "id": s.id}

    def _add_open(self, pts: list[Pt]) -> dict:
        line, explicit = split_head(pts)
        a = self.shape_at(line[0], SNAP)
        b = self.shape_at(line[-1], SNAP)
        length = path_len(line)
        host = self._host(pts)
        if host is not None:
            # внутри группы стрелка между её узлами — стрелка; всё остальное — подпись от руки
            inner = {s.id for s in self.descendants(host)}
            a2 = a if a is not None and a.id in inner else None
            b2 = b if b is not None and b.id in inner else None
            if a2 is not None and b2 is not None and a2.id != b2.id and length >= 30:
                return self._make_edge(line, a2, b2, explicit)
            if (a2 is not None) != (b2 is not None) and length >= 40 and (a2 or b2).id != host.id:
                return self._make_edge(line, a2, b2, explicit)
            return self._hand(host, pts)
        if a is not None and b is not None and a.id == b.id:
            # вышли из фигуры далеко и вернулись — петля; чуть вылезли за край — это подпись
            out = max((dist(p, a.border_point(p)) for p in pts if not a.contains(p)), default=0.0)
            if out >= 24 and length >= 60:
                return self._make_edge(line, a, a, explicit)
            return self._hand(a, pts)
        if a is not None and b is not None and length >= 24 and _diag(pts) >= 16:
            return self._make_edge(line, a, b, explicit)
        if (a is not None or b is not None) and length >= 30:
            return self._make_edge(line, a, b, explicit)
        label_of = self._edge_near(pts)
        if label_of is not None and _diag(pts) <= 140:
            m = self.edge_mid(label_of)
            label_of.hand.append([(p[0] - m[0], p[1] - m[1]) for p in pts])
            return {"kind": "edge_hand", "id": label_of.id}
        if length >= 60 and dist(line[0], line[-1]) >= 0.85 * length:
            return self._make_edge(line, None, None, explicit)     # ровная линия: стрелка, которая ждёт фигур
        k = Ink(self._id(), pts)
        self.ink[k.id] = k
        return {"kind": "ink", "id": k.id}

    def _hand(self, s: Shape, pts: list[Pt]) -> dict:
        s.hand.append([(p[0] - s.x, p[1] - s.y) for p in pts])
        return {"kind": "hand", "id": s.id}

    def _edge_near(self, pts: list[Pt]) -> Edge | None:
        """Стрелка, рядом с серединой которой написан штрих (её подпись от руки)."""
        x0, y0, x1, y1 = bbox(pts)
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        best, found = 1e9, None
        for e in self.edges.values():
            path = self.edge_path(e)
            middle = [point_at(path, f / 10) for f in range(2, 9)]
            d = min(poly_dist(p, middle) for p in pts)
            if d <= 30 and dist(c, self.edge_mid(e)) <= 0.5 * path_len(path) + 40 and d < best:
                best, found = d, e
        return found

    @staticmethod
    def _crossing(s: Shape, inside: Pt, outside: Pt) -> Pt:
        lo, hi = 0.0, 1.0
        for _ in range(18):
            mid = (lo + hi) / 2
            p = (inside[0] + mid * (outside[0] - inside[0]), inside[1] + mid * (outside[1] - inside[1]))
            if s.contains(p):
                lo = mid
            else:
                hi = mid
        return (inside[0] + hi * (outside[0] - inside[0]), inside[1] + hi * (outside[1] - inside[1]))

    def _make_edge(self, pts: list[Pt], a: Shape | None, b: Shape | None, explicit: bool) -> dict:
        line = list(pts)
        if a is not None:
            i = next((k for k, p in enumerate(line) if not a.contains(p)), None)
            if i is None:
                line = []
            elif i > 0:
                line = [self._crossing(a, line[i - 1], line[i])] + line[i:]
        if b is not None and line:
            j = next((k for k in range(len(line) - 1, -1, -1) if not b.contains(line[k])), None)
            if j is None:
                line = []
            elif j < len(line) - 1:
                line = line[:j + 1] + [self._crossing(b, line[j + 1], line[j])]
        e = Edge(self._id(), a.id if a else None, b.id if b else None, head_b=True, explicit_b=explicit)
        if len(line) >= 2 and path_len(line) >= 6:
            e.ink = line
            if a is not None:
                start = line[0] if a.contains(line[0], 1) else a.border_point(line[0])
                e.ua = ((start[0] - a.x) / a.w, (start[1] - a.y) / a.h)
            if b is not None:
                end = line[-1] if b.contains(line[-1], 1) else b.border_point(line[-1])
                e.ub = ((end[0] - b.x) / b.w, (end[1] - b.y) / b.h)
        e.pa, e.pb = (line[0], line[-1]) if line else (pts[0], pts[-1])
        self.edges[e.id] = e
        # подпись написали заранее, потом провели стрелку под ней
        for k in list(self.ink.values()):
            if _diag(k.pts) <= 140 and self._edge_near(k.pts) is e:
                m = self.edge_mid(e)
                e.hand.append([(p[0] - m[0], p[1] - m[1]) for p in k.pts])
                del self.ink[k.id]
        return {"kind": "edge", "id": e.id}

    def bind_free_ends(self) -> int:
        """Свободные концы стрелок, оказавшиеся в фигуре, привязываются к ней."""
        n = 0
        for e in self.edges.values():
            for which in ("a", "b"):
                if (e.a if which == "a" else e.b) is not None:
                    continue
                p = e.pa if which == "a" else e.pb
                s = self.shape_at(p, 10)
                other = e.b if which == "a" else e.a
                if s is None or (other == s.id and len(e.ink) < 2):
                    continue
                bp = p if s.contains(p, 1) else s.border_point(p)
                u = ((bp[0] - s.x) / s.w, (bp[1] - s.y) / s.h) if len(e.ink) >= 2 else None
                if which == "a":
                    e.a, e.ua = s.id, u
                else:
                    e.b, e.ub = s.id, u
                n += 1
        return n

    # ------------------------------------------------------------- правки
    def add_shape(self, kind: str, x: float, y: float, w: float, h: float, label: str = "") -> int:
        s = Shape(self._id(), kind if kind in SHAPE_KINDS else "rect", x, y, w, h, label=label)
        self.shapes[s.id] = s
        self.bind_free_ends()
        return s.id

    def add_edge(self, a: int | None, b: int | None, label: str = "", pa: Pt = (0, 0), pb: Pt = (0, 0),
                 style: str = "solid", head_a: bool = False, head_b: bool = True) -> int:
        e = Edge(self._id(), a, b, pa=pa, pb=pb, label=label, style=style, head_a=head_a, head_b=head_b,
                 explicit_b=True)
        self.edges[e.id] = e
        return e.id

    def add_note(self, x: float, y: float, text: str) -> int:
        n = Note(self._id(), x, y, text)
        self.notes[n.id] = n
        return n.id

    def set_label(self, kind: str, item_id: int, text: str) -> None:
        text = " ".join((text or "").split())
        if kind == "shape" and item_id in self.shapes:
            self.shapes[item_id].label = text
        elif kind == "edge" and item_id in self.edges:
            self.edges[item_id].label = text
        elif kind == "note" and item_id in self.notes:
            if text:
                self.notes[item_id].text = text
            else:
                del self.notes[item_id]

    def delete(self, kind: str, item_id: int) -> None:
        if kind == "shape" and item_id in self.shapes:
            for e in self.edges.values():               # стрелки остаются и ждут новую фигуру на том же месте
                if e.a == item_id:
                    e.pa, e.a, e.ua = self.endpoint(e, "a"), None, None
                if e.b == item_id:
                    e.pb, e.b, e.ub = self.endpoint(e, "b"), None, None
            del self.shapes[item_id]
        elif kind == "edge":
            self.edges.pop(item_id, None)
        elif kind == "note":
            self.notes.pop(item_id, None)
        elif kind == "ink":
            self.ink.pop(item_id, None)

    def expand(self, items: set[tuple[str, int]]) -> set[tuple[str, int]]:
        """Что едет вместе с выбранным: у группы — всё, что внутри."""
        out = set(items)
        for kind, i in items:
            if kind == "shape" and i in self.shapes:
                g = self.shapes[i]
                for c in self.descendants(g):
                    out.add(("shape", c.id))
                for k in self.ink.values():
                    if all(g.contains(p) for p in k.pts):
                        out.add(("ink", k.id))
                for n in self.notes.values():
                    if g.contains((n.x, n.y)):
                        out.add(("note", n.id))
        return out

    def move(self, items: set[tuple[str, int]], dx: float, dy: float) -> None:
        for kind, i in items:
            if kind == "shape" and i in self.shapes:
                self.shapes[i].x += dx
                self.shapes[i].y += dy
            elif kind == "note" and i in self.notes:
                self.notes[i].x += dx
                self.notes[i].y += dy
            elif kind == "ink" and i in self.ink:
                self.ink[i].pts = [(p[0] + dx, p[1] + dy) for p in self.ink[i].pts]
            elif kind == "edge" and i in self.edges:
                e = self.edges[i]
                if e.a is None:
                    e.pa = (e.pa[0] + dx, e.pa[1] + dy)
                if e.b is None:
                    e.pb = (e.pb[0] + dx, e.pb[1] + dy)
                if e.a is None and e.b is None:
                    e.ink = [(p[0] + dx, p[1] + dy) for p in e.ink]

    def move_end(self, edge_id: int, which: str, p: Pt) -> None:
        """Перетащили конец стрелки: к фигуре под ним — привязать, иначе оставить свободным."""
        e = self.edges.get(edge_id)
        if e is None:
            return
        s = self.shape_at(p, 8)
        u = None
        if s is not None and len(e.ink) >= 2:
            bp = s.border_point(p)
            u = ((bp[0] - s.x) / s.w, (bp[1] - s.y) / s.h)
        if which == "a":
            e.a, e.ua, e.pa = (s.id if s else None), u, p
        else:
            e.b, e.ub, e.pb = (s.id if s else None), u, p

    def reverse(self, edge_id: int) -> None:
        e = self.edges.get(edge_id)
        if e is None:
            return
        e.a, e.b, e.ua, e.ub, e.pa, e.pb = e.b, e.a, e.ub, e.ua, e.pb, e.pa
        e.ink = list(reversed(e.ink))
        e.head_a, e.head_b = e.head_b, e.head_a
        e.explicit_b = True

    def erase(self, p: Pt, r: float = 9.0) -> bool:
        """Ластик: сначала подписи и рисунок от руки, потом стрелки, потом фигуры (по контуру)."""
        hit = False
        for s in self.shapes.values():
            keep = [st for st in s.hand if poly_dist(p, [(s.x + q[0], s.y + q[1]) for q in st]) > r]
            hit |= len(keep) != len(s.hand)
            s.hand = keep
        for e in self.edges.values():
            m = self.edge_mid(e)
            keep = [st for st in e.hand if poly_dist(p, [(m[0] + q[0], m[1] + q[1]) for q in st]) > r]
            hit |= len(keep) != len(e.hand)
            e.hand = keep
        for k in list(self.ink.values()):
            if poly_dist(p, k.pts) <= r:
                del self.ink[k.id]
                hit = True
        for n in list(self.notes.values()):
            if n.x - 4 <= p[0] <= n.x + max(30, 7.5 * len(n.text)) and n.y - 4 <= p[1] <= n.y + 22:
                del self.notes[n.id]
                hit = True
        if hit:
            return True
        for e in list(self.edges.values()):
            if poly_dist(p, self.edge_path(e)) <= r:
                del self.edges[e.id]
                hit = True
        if hit:
            return True
        for s in sorted(self.shapes.values(), key=lambda s: s.area):
            if any(poly_dist(p, st) <= r for st in s.outline()):
                self.delete("shape", s.id)
                return True
        return False

    def hit(self, p: Pt, tol: float = 8.0) -> tuple[str, int] | None:
        """Что под точкой (для выбора, подписи и меню)."""
        for n in self.notes.values():
            if n.x - 4 <= p[0] <= n.x + max(30, 7.5 * len(n.text)) and n.y - 4 <= p[1] <= n.y + 22:
                return ("note", n.id)
        best, found = tol, None
        for e in self.edges.values():
            d = poly_dist(p, self.edge_path(e))
            if e.label or e.hand:
                d = min(d, dist(p, self.edge_mid(e)) - 6)
            if d <= best:
                best, found = d, ("edge", e.id)
        if found:
            return found
        for k in self.ink.values():
            if poly_dist(p, k.pts) <= tol:
                return ("ink", k.id)
        s = self.shape_at(p, 4)
        return ("shape", s.id) if s else None

    def ink_to_shape(self, ink_id: int) -> int | None:
        """«Это фигура»: рисунок, который не распознался замкнутым, — превратить в фигуру."""
        k = self.ink.get(ink_id)
        if k is None:
            return None
        x0, y0, x1, y1 = bbox(k.pts)
        if min(x1 - x0, y1 - y0) < 10:
            return None
        del self.ink[ink_id]
        return self._add_closed(k.pts + [k.pts[0]])["id"]

    def edge_to_ink(self, edge_id: int) -> None:
        """«Это рисунок, не стрелка»."""
        e = self.edges.pop(edge_id, None)
        if e is not None:
            path = self.edge_path(e)
            k = Ink(self._id(), path)
            self.ink[k.id] = k

    def hand_to_ink(self, shape_id: int) -> None:
        s = self.shapes.get(shape_id)
        if s is not None:
            for st in s.hand_abs():
                k = Ink(self._id(), st)
                self.ink[k.id] = k
            s.hand = []


# ================================================================ Mermaid
def _q(text: str) -> str:
    """Подпись для Mermaid: в кавычках, без кавычек внутри."""
    return '"' + (text or "").replace('"', "'").replace("\n", " ").strip() + '"'


def reading_order(board: Board) -> list[Shape]:
    """Сверху вниз и слева направо, как читают схему."""
    return sorted(board.shapes.values(), key=lambda s: (round(s.cy / 40), s.cx))


def direction(board: Board) -> str:
    dx = dy = 0.0
    for e in board.edges.values():
        a, b = board.shapes.get(e.a), board.shapes.get(e.b)
        if a and b:
            dx += abs(b.cx - a.cx)
            dy += abs(b.cy - a.cy)
    return "LR" if dx > dy else "TD"


def _node_text(s: Shape) -> str:
    if s.label.strip():
        return s.label.strip() + (" ✎" if s.hand else "")
    return "✎" if s.hand else "(без подписи)"


def _node_decl(s: Shape) -> str:
    t = _q(_node_text(s))
    return {"ellipse": f"n{s.id}([{t}])", "circle": f"n{s.id}(({t}))", "diamond": f"n{s.id}{{{t}}}",
            "round": f"n{s.id}({t})"}.get(s.kind, f"n{s.id}[{t}]")


_ARROWS = {"solid": ("---", "-->", "<-->"), "dotted": ("-.-", "-.->", "<-.->"), "thick": ("===", "==>", "<==>")}


def _link(e: Edge, label: str) -> str:
    plain, one, both = _ARROWS.get(e.style, _ARROWS["solid"])
    arrow = both if e.head_a and e.head_b else one if e.head_a or e.head_b else plain
    return arrow + (f"|{_q(label)}|" if label else "")


def edge_text(e: Edge) -> str:
    if e.label.strip():
        return e.label.strip() + (" ✎" if e.hand else "")
    return "✎" if e.hand else ""


def to_mermaid(board: Board, image: bool = False) -> str:
    """Структура доски в Mermaid (flowchart). Порядок — как читают схему; ключи узлов не меняются."""
    order = reading_order(board)
    rank = {s.id: k for k, s in enumerate(order)}
    lines = [f"flowchart {direction(board)}"]
    pm = board.parent_map()
    kids: dict[int | None, list[Shape]] = {}
    for s in order:
        kids.setdefault(pm.get(s.id), []).append(s)

    def emit(pid: int | None, indent: str) -> None:
        for s in kids.get(pid, []):
            if s.id in kids:
                lines.append(f"{indent}subgraph n{s.id}[{_q(_node_text(s) if (s.label or s.hand) else ' ')}]")
                emit(s.id, indent + "  ")
                lines.append(f"{indent}end")
            else:
                lines.append(indent + _node_decl(s))
    emit(None, "  ")
    edges = sorted(board.edges.values(), key=lambda e: (rank.get(e.a, 1e9), rank.get(e.b, 1e9), e.id))
    loose = []
    for e in edges:
        if e.a is None or e.b is None:
            loose.append(e)
            continue
        a, b = e.a, e.b
        if e.head_a and not e.head_b:                     # стрелка нарисована «обратно» — пишем по смыслу
            a, b = b, a
            e = copy.copy(e)
            e.head_a, e.head_b = False, True
        lines.append(f"  n{a} {_link(e, edge_text(e))} n{b}")
    for e in loose:
        if e.a is not None:
            lines.append(f"  %% стрелка из n{e.a} ни к чему не привязана")
        elif e.b is not None:
            lines.append(f"  %% стрелка в n{e.b} ниоткуда (начало не привязано)")
        else:
            lines.append("  %% стрелка без начала и конца")
    for n in sorted(board.notes.values(), key=lambda n: (round(n.y / 40), n.x)):
        near = min(board.shapes.values(), key=lambda s: dist((n.x, n.y), (s.cx, s.cy)), default=None)
        where = f" (рядом с n{near.id})" if near and dist((n.x, n.y), (near.cx, near.cy)) < 160 else ""
        lines.append(f"  %% надпись на доске{where}: {n.text}")
    st = board.stats()
    if st["hand"] or st["ink"]:
        what = []
        if st["hand"]:
            what.append("✎ — подпись от руки")
        if st["ink"]:
            what.append(f"ещё {st['ink']} {plural(st['ink'], ('штрих', 'штриха', 'штрихов'))} рисунка от руки")
        lines.append("  %% " + "; ".join(what) + (" — смотри на картинке доски" if image
                                                  else " (картинка не приложена)"))
    return "\n".join(lines)


def describe(board: Board) -> str:
    """Коротко, что распознано: «4 узла, 3 стрелки, 1 группа»."""
    st = board.stats()
    parts = [f"{st['nodes']} {plural(st['nodes'], ('узел', 'узла', 'узлов'))}",
             f"{st['edges']} {plural(st['edges'], ('стрелка', 'стрелки', 'стрелок'))}"]
    if st["groups"]:
        parts.append(f"{st['groups']} {plural(st['groups'], ('группа', 'группы', 'групп'))}")
    return ", ".join(parts)


def diff(prev: Board, cur: Board) -> list[str]:
    """Что изменилось с прошлой отправки — коротко, по ключам узлов."""
    out: list[str] = []
    for i, s in cur.shapes.items():
        if i not in prev.shapes:
            out.append(f"добавлен n{i} «{_node_text(s)}»")
        elif _node_text(prev.shapes[i]) != _node_text(s):
            out.append(f"n{i}: «{_node_text(prev.shapes[i])}» → «{_node_text(s)}»")
    for i, s in prev.shapes.items():
        if i not in cur.shapes:
            out.append(f"убран n{i} «{_node_text(s)}»")

    def pairs(b: Board) -> dict[tuple, str]:
        res = {}
        for e in b.edges.values():
            if e.a is not None and e.b is not None:
                a, bb = (e.b, e.a) if e.head_a and not e.head_b else (e.a, e.b)
                res[(a, bb)] = edge_text(e)
        return res
    pp, cp = pairs(prev), pairs(cur)
    for (a, b), t in cp.items():
        if (a, b) not in pp:
            out.append(f"связь n{a} → n{b}" + (f" «{t}»" if t else ""))
        elif pp[(a, b)] != t:
            out.append(f"подпись связи n{a} → n{b}: «{t}»")
    for (a, b) in pp:
        if (a, b) not in cp:
            out.append(f"убрана связь n{a} → n{b}")
    return out


def board_prompt(board: Board, comment: str = "", image: bool = False, prev: Board | None = None) -> str:
    """Сообщение для Claude: комментарий человека и схема с доски в Mermaid."""
    parts = []
    if comment.strip():
        parts.append(comment.strip())
    parts.append(f"[доска] Схема нарисована от руки; приложение распознало {describe(board)}:\n"
                 f"```mermaid\n{to_mermaid(board, image)}\n```")
    if prev is not None and not prev.is_empty():
        changes = diff(prev, board)
        parts.append("Что изменилось с прошлой схемы: " + ("; ".join(changes[:12]) if changes else "ничего") + ".")
    if image:
        parts.append("Подписи и рисунок от руки (✎) — на приложенной картинке доски.")
    return "\n\n".join(parts)


# ================================================================ Mermaid → схема (ответы Claude)
_ID = re.compile(r"\s*([\wЀ-ӿ]+)")
_OPENERS = (("([", "])", "ellipse"), ("((", "))", "circle"), ("[[", "]]", "rect"), ("[(", ")]", "rect"),
            ("{{", "}}", "diamond"), ("[/", "/]", "rect"), ("[\\", "\\]", "rect"), ("[/", "\\]", "rect"),
            ("[", "]", "rect"), ("(", ")", "round"), ("{", "}", "diamond"), (">", "]", "rect"))
_LINK = re.compile(r"""\s*(?:
    (?P<l2><)?(?P<o2>--|==|-\.)\s*(?P<t2>[^\s>|=.-][^>|]*?)\s*(?P<c2>-{2,}[>ox]?|={2,}[>ox]?|\.-+[>ox]?)
  | (?P<l1><)?(?P<b1>-{2,}|={2,}|-\.+-)(?P<r1>[>ox])?\s*(?:\|(?P<t1>[^|]*)\|)?
)\s*""", re.X)
_KEYWORDS = {"end", "subgraph", "classDef", "class", "style", "linkStyle", "click", "direction"}


def _clean(text: str) -> str:
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        t = t[1:-1]
    t = re.sub(r"<br\s*/?>", " ", t)
    t = t.replace("#quot;", "'").replace("&quot;", "'")
    t = re.sub(r"<[^>]+>", "", t)
    return " ".join(t.split())


def _split(line: str) -> list[str]:
    out, buf, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [s for s in (x.strip() for x in out) if s]


def parse_mermaid(text: str) -> dict | None:
    """Разбор flowchart/graph: {'dir', 'nodes': {id: {label, kind}}, 'edges': [...], 'groups': [...]}.
    Не схема Mermaid — None (тогда это просто код)."""
    lines = [ln.strip() for ln in (text or "").replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln and not ln.startswith("%%")]
    if not lines:
        return None
    m = re.match(r"^(flowchart|graph)\s*(TB|TD|BT|LR|RL)?\s*(?:;(.*))?$", lines[0], re.I)
    if not m:
        return None
    lines = [""] + ([m.group(3)] if m.group(3) and m.group(3).strip() else []) + lines[1:]
    g = {"dir": (m.group(2) or "TD").upper().replace("TB", "TD"), "nodes": {}, "edges": [], "groups": []}
    stack: list[dict] = []

    def node(s: str, i: int) -> tuple[str | None, int]:
        mm = _ID.match(s, i)
        if not mm or mm.group(1) in _KEYWORDS:
            return None, i
        nid, j = mm.group(1), mm.end()
        for op, cl, kind in _OPENERS:
            if s.startswith(op, j):
                k = j + len(op)
                if s[k:k + 1] == '"':
                    q = s.find('"', k + 1)
                    end = s.find(cl, q + 1 if q > 0 else k)
                else:
                    end = s.find(cl, k)
                if end < 0:
                    break
                label = _clean(s[k:end])
                n = g["nodes"].setdefault(nid, {"label": nid, "kind": "rect", "group": None})
                n["label"], n["kind"] = label or nid, kind
                j = end + len(cl)
                break
        n = g["nodes"].setdefault(nid, {"label": nid, "kind": "rect", "group": None})
        if stack and n["group"] is None:
            n["group"] = stack[-1]["id"]
            stack[-1]["members"].append(nid)
        return nid, j

    def nodes(s: str, i: int) -> tuple[list[str], int]:
        ids = []
        while True:
            nid, i = node(s, i)
            if nid is None:
                break
            ids.append(nid)
            mm = re.match(r"\s*&\s*", s[i:])
            if not mm:
                break
            i += mm.end()
        return ids, i

    for raw in lines[1:]:
        raw = re.sub(r":::[\w-]+", "", raw)
        for st in _split(raw):
            if st == "end":
                if stack:
                    stack.pop()
                continue
            if st.startswith("subgraph"):
                rest = st[len("subgraph"):].strip()
                mm = re.match(r"^([\wЀ-ӿ]+)\s*\[(.*)\]\s*$", rest)
                gid, title = (mm.group(1), _clean(mm.group(2))) if mm else (f"_g{len(g['groups'])}", _clean(rest))
                grp = {"id": gid, "label": title, "members": [], "parent": stack[-1]["id"] if stack else None}
                g["groups"].append(grp)
                if stack:
                    stack[-1]["members"].append(gid)
                stack.append(grp)
                continue
            first = st.split()[0] if st.split() else ""
            if first in _KEYWORDS:
                continue
            left, i = nodes(st, 0)
            while left:
                lm = _LINK.match(st, i)
                if not lm or lm.end() == i:
                    break
                i = lm.end()
                right, i = nodes(st, i)
                if not right:
                    break
                if lm.group("b1"):
                    body, ra, la, label = lm.group("b1"), bool(lm.group("r1")), bool(lm.group("l1")), lm.group("t1")
                else:
                    body, ra, la = lm.group("c2"), lm.group("c2")[-1] in ">ox", bool(lm.group("l2"))
                    label = lm.group("t2")
                    body = lm.group("o2") + body
                style = "dotted" if "." in body else "thick" if "=" in body else "solid"
                for a in left:
                    for b in right:
                        g["edges"].append({"a": a, "b": b, "label": _clean(label or ""), "style": style,
                                           "head_a": la, "head_b": ra})
                left = right
    group_ids = {gr["id"] for gr in g["groups"]}
    for gid in group_ids:
        g["nodes"].pop(gid, None)                      # стрелка к подграфу — к подграфу, не к узлу
    if not g["nodes"] and not g["groups"]:
        return None
    return g


def _text_size(label: str) -> tuple[float, float]:
    """Примерный размер подписи без Qt (окно передаёт точный, см. measure): ~8 px на знак, перенос после 20."""
    words, lines, cur = label.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 20:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return max((len(x) for x in lines), default=4) * 8.0, 17.0 * len(lines)


def node_size(kind: str, text_w: float, text_h: float) -> tuple[float, float]:
    """Размер фигуры, в которую подпись влезает целиком (у ромба и круга место для текста — середина)."""
    if kind == "diamond":
        return max(110.0, (text_w + 14) / 0.58), max(64.0, (text_h + 10) * 1.9)
    if kind == "circle":
        d = max(64.0, (text_w + 10) / 0.62, text_h + 34)
        return d, d
    if kind in ("ellipse", "round"):
        return max(90.0, text_w / 0.86 + 36), text_h + 22
    return max(80.0, text_w / 0.9 + 24), text_h + 22


def layout_graph(g: dict, measure=None) -> Board:
    """Разложить граф Mermaid по слоям (как Sugiyama: слои, промежуточные точки длинных стрелок,
    барицентры, выравнивание) и собрать доску из ровных фигур — для показа и правки.
    measure(подпись) → (ширина, высота) текста; без него — оценка по числу знаков."""
    measure = measure or _text_size
    ids = list(g["nodes"])
    group_ids = [gr["id"] for gr in g["groups"]]
    real_edges = [e for e in g["edges"] if e["a"] in g["nodes"] and e["b"] in g["nodes"]]
    out: dict[str, list[str]] = {i: [] for i in ids}
    inn: dict[str, list[str]] = {i: [] for i in ids}
    for e in real_edges:
        if e["a"] != e["b"]:
            out[e["a"]].append(e["b"])
            inn[e["b"]].append(e["a"])
    # слои — длиннейший путь от истоков; обратные рёбра циклов не считаем
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()

    def visit(v: str) -> None:
        state[v] = 1
        for w in out[v]:
            if state.get(w) == 1:
                back.add((v, w))
            elif not state.get(w):
                visit(w)
        state[v] = 2
    for v in ids:
        if not state.get(v):
            visit(v)
    indeg = {v: sum(1 for u in inn[v] if (u, v) not in back) for v in ids}
    queue = [v for v in ids if indeg[v] == 0]
    topo: list[str] = []
    while queue:
        v = queue.pop(0)
        topo.append(v)
        for w in out[v]:
            if (v, w) not in back:
                indeg[w] -= 1
                if indeg[w] == 0:
                    queue.append(w)
    layer: dict[str, int] = {}
    for v in topo:
        layer[v] = max([layer[u] + 1 for u in inn[v] if (u, v) not in back and u in layer], default=0)
    for v in ids:
        layer.setdefault(v, 0)
    # длинные стрелки — через промежуточные точки на каждом слое: так они обходят узлы, а не режут их
    nbr: dict[str, list[str]] = {v: [] for v in ids}
    chains: dict[int, list[str]] = {}
    for k, e in enumerate(real_edges):
        a, b = e["a"], e["b"]
        if a == b:
            continue
        la, lb = layer[a], layer[b]
        if abs(lb - la) <= 1:
            nbr[a].append(b)
            nbr[b].append(a)
            continue
        step = 1 if lb > la else -1
        prev, chain = a, []
        for lv in range(la + step, lb, step):
            d = f"\x00{k}:{lv}"
            layer[d] = lv
            nbr[d] = [prev]
            nbr[prev].append(d)
            chain.append(d)
            prev = d
        nbr[prev].append(b)
        nbr[b].append(prev)
        chains[k] = chain
    layers: dict[int, list[str]] = {}
    for v in ids + [d for ch in chains.values() for d in ch]:
        layers.setdefault(layer[v], []).append(v)
    group_of = {v: g["nodes"][v].get("group") for v in ids}
    group_rank = {gid: k for k, gid in enumerate(group_ids)}
    order = {v: float(k) for vs in layers.values() for k, v in enumerate(vs)}
    for sweep in range(6):                              # барицентры: меньше пересечений
        lvls = sorted(layers) if sweep % 2 == 0 else sorted(layers, reverse=True)
        for lv in lvls:
            def key(v, lv=lv):
                nb = [order[u] for u in nbr[v] if layer[u] != lv]
                return (group_rank.get(group_of.get(v), -1), sum(nb) / len(nb) if nb else order[v])
            layers[lv].sort(key=key)
            for k, v in enumerate(layers[lv]):
                order[v] = float(k)
    horizontal = g["dir"] in ("LR", "RL")
    sizes: dict[str, tuple[float, float]] = {}
    for v in ids:
        tw, th = measure(g["nodes"][v]["label"])
        sizes[v] = node_size(g["nodes"][v]["kind"], tw, th)
    for ch in chains.values():
        for d in ch:
            sizes[d] = (12.0, 12.0)
    span = {v: (sizes[v][1] if horizontal else sizes[v][0]) for v in sizes}       # поперёк слоёв
    nlay = max(layers) + 1 if layers else 0
    thick = [max([(sizes[v][0] if horizontal else sizes[v][1]) for v in layers.get(k, []) if v in g["nodes"]],
                 default=12.0) for k in range(nlay)]

    def sep(a: str, b: str) -> float:
        gap = 28.0 if a in g["nodes"] and b in g["nodes"] else 16.0
        return (span[a] + span[b]) / 2 + gap
    # поперечные координаты: каждый узел тянется к середине соседей, порядок и зазоры сохраняются
    cross: dict[str, float] = {}
    for lv, vs in layers.items():
        x = 0.0
        for i, v in enumerate(vs):
            x = x + sep(vs[i - 1], v) if i else 0.0
            cross[v] = x
    for sweep in range(8):
        lvls = sorted(layers) if sweep % 2 == 0 else sorted(layers, reverse=True)
        for lv in lvls:
            vs = layers[lv]
            want = []
            for v in vs:
                nb = [cross[u] for u in nbr[v] if layer[u] != lv]
                want.append(sum(nb) / len(nb) if nb else cross[v])
            pos = list(want)
            for i in range(1, len(vs)):
                pos[i] = max(pos[i], pos[i - 1] + sep(vs[i - 1], vs[i]))
            shift = sum(w - p for w, p in zip(want, pos)) / len(vs)
            for v, p in zip(vs, pos):
                cross[v] = p + shift
    low = min((cross[v] - span[v] / 2 for v in cross), default=0.0)
    main_at, run = [], 30.0
    for k in range(nlay):
        main_at.append(run)
        run += thick[k] + 66.0

    def place(v: str) -> tuple[float, float]:
        """Центр узла или промежуточной точки на доске."""
        c = cross[v] - low + 30.0
        m = main_at[layer[v]] + thick[layer[v]] / 2
        return (m, c) if horizontal else (c, m)
    board = Board()
    sid: dict[str, int] = {}
    for v in ids:
        cx, cy = place(v)
        w, h = sizes[v]
        sid[v] = board.add_shape(g["nodes"][v]["kind"], cx - w / 2, cy - h / 2, w, h, g["nodes"][v]["label"])
    flip = g["dir"] in ("BT", "RL")
    bb = board.bounds() or (0, 0, 0, 0)

    def flipped(p: Pt) -> Pt:
        if not flip:
            return p
        return (p[0], bb[1] + bb[3] - p[1]) if g["dir"] == "BT" else (bb[0] + bb[2] - p[0], p[1])
    if flip:                                            # снизу вверх / справа налево — отражаем
        for s in board.shapes.values():
            cx, cy = flipped((s.cx, s.cy))
            s.x, s.y = cx - s.w / 2, cy - s.h / 2
    # группы — рамки вокруг своих узлов (вложенные — шире)
    members: dict[str, list[str]] = {gr["id"]: list(gr["members"]) for gr in g["groups"]}

    def flat(gid: str, seen: frozenset = frozenset()) -> list[str]:
        res = []
        for m in members.get(gid, []):
            if m in members and m not in seen:
                res += flat(m, seen | {gid})
            elif m in sid:
                res.append(m)
        return res

    def levels(gid: str, seen: frozenset = frozenset()) -> int:
        subs = [m for m in members.get(gid, []) if m in members and m not in seen]
        return 1 + max((levels(m, seen | {gid}) for m in subs), default=0)
    gsid: dict[str, int] = {}
    for gr in sorted(g["groups"], key=lambda gr: levels(gr["id"])):
        inner = flat(gr["id"])
        if not inner:
            continue
        pad = 14.0 * levels(gr["id"])
        boxes = [board.shapes[sid[m]] for m in inner]
        x0 = min(s.x for s in boxes) - pad
        y0 = min(s.y for s in boxes) - pad - 18
        x1 = max(s.x + s.w for s in boxes) + pad
        y1 = max(s.y + s.h for s in boxes) + pad
        gsid[gr["id"]] = board.add_shape("rect", x0, y0, x1 - x0, y1 - y0, gr["label"])
    by_edge = {id(e): k for k, e in enumerate(real_edges)}
    for e in g["edges"]:
        a = sid.get(e["a"], gsid.get(e["a"]))
        b = sid.get(e["b"], gsid.get(e["b"]))
        if a is None or b is None:
            continue
        eid = board.add_edge(a, b, e["label"], style=e["style"], head_a=e["head_a"], head_b=e["head_b"])
        chain = chains.get(by_edge.get(id(e), -1))
        if chain:                                       # путь в обход узлов — росчерком, привязанным к краям
            sa, sb = board.shapes[a], board.shapes[b]
            mids = [flipped(place(d)) for d in chain]
            start, end = sa.border_point(mids[0]), sb.border_point(mids[-1])
            edge = board.edges[eid]
            edge.ink = [start, *mids, end]
            edge.ua = ((start[0] - sa.x) / sa.w, (start[1] - sa.y) / sa.h)
            edge.ub = ((end[0] - sb.x) / sb.w, (end[1] - sb.y) / sb.h)
    return board


def from_mermaid(text: str, measure=None) -> Board | None:
    g = parse_mermaid(text)
    return layout_graph(g, measure) if g else None
