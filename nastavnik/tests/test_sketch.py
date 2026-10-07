"""Доска: рисунок от руки → структура. Росчерки синтетические, но с дрожанием, недолётом и перелётом,
как у живой руки; каждое правило привязки проверяется тем, что получает Claude, — Mermaid."""
from __future__ import annotations

import math
import random

import pytest

from nastavnik import sketch
from nastavnik.sketch import Board, classify_closed, from_mermaid, is_closed, parse_mermaid, to_mermaid


# ---------------------------------------------------------------- «рука»
def wobble(pts, rng, amp=1.4):
    return [(x + rng.uniform(-amp, amp), y + rng.uniform(-amp, amp)) for x, y in pts]


def segment(a, b, step=4.0):
    n = max(1, int(math.dist(a, b) / step))
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]


def polyline(pts, step=4.0):
    out = []
    for i in range(len(pts) - 1):
        out += segment(pts[i], pts[i + 1], step)[:-1]
    return out + [pts[-1]]


def rect(x, y, w, h, rng, gap=0.0, rot=0.0):
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + gap, y + (8 if gap < 0 else 0))]
    pts = polyline(corners)
    if gap < 0:                                    # перелёт: заехали дальше начала
        pts += segment((x, y), (x - gap, y))[1:]
    if rot:
        cx, cy = x + w / 2, y + h / 2
        c, s = math.cos(rot), math.sin(rot)
        pts = [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c) for px, py in pts]
    return wobble(pts, rng)


def ellipse(cx, cy, rx, ry, rng, sweep=1.04, start=0.3):
    n = 60
    return wobble([(cx + rx * math.cos(start + 2 * math.pi * sweep * k / n),
                    cy + ry * math.sin(start + 2 * math.pi * sweep * k / n)) for k in range(n + 1)], rng)


def diamond(cx, cy, rx, ry, rng):
    return wobble(polyline([(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy), (cx + 3, cy - ry + 2)]), rng)


def line(a, b, rng, bend=0.0):
    pts = segment(a, b)
    if bend:
        n = len(pts) - 1
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln, dx / ln
        pts = [(x + nx * bend * math.sin(math.pi * k / n), y + ny * bend * math.sin(math.pi * k / n))
               for k, (x, y) in enumerate(pts)]
    return wobble(pts, rng, 0.8)


def vee(tip, direction, size=12):
    """Галочка-наконечник: два отрезка назад от острия."""
    ang = math.atan2(direction[1], direction[0])
    l = (tip[0] - size * math.cos(ang - 0.5), tip[1] - size * math.sin(ang - 0.5))
    r = (tip[0] - size * math.cos(ang + 0.5), tip[1] - size * math.sin(ang + 0.5))
    return polyline([l, tip, r], 2)


def scribble(x, y, w=26, h=12, rng=None):
    """Буква от руки."""
    rng = rng or random.Random(1)
    return wobble([(x + w * k / 10, y + h * (0.5 + 0.5 * math.sin(k * 1.7))) for k in range(11)], rng, 0.6)


def two_boxes(b: Board, rng, y2=40):
    a = b.add_stroke(rect(40, 40, 140, 60, rng))
    c = b.add_stroke(rect(360, y2, 140, 60, rng))
    return a["id"], c["id"]


# ---------------------------------------------------------------- фигуры
@pytest.mark.parametrize("seed", range(12))
def test_shapes_are_recognized_like_drawn(seed):
    rng = random.Random(seed)
    cases = [
        (rect(10, 10, 160, 70, rng), "rect"),
        (rect(10, 10, 120, 90, rng, gap=10), "rect"),             # недолёт
        (rect(10, 10, 140, 60, rng, gap=-14), "rect"),            # перелёт
        (rect(10, 10, 140, 60, rng, rot=0.06), "rect"),           # чуть наискось
        (ellipse(100, 60, 90, 40, rng), "ellipse"),
        (ellipse(100, 60, 90, 40, rng, sweep=0.95), "ellipse"),   # не дотянули
        (ellipse(80, 80, 50, 46, rng), "circle"),
        (diamond(100, 70, 70, 50, rng), "diamond"),
    ]
    for pts, kind in cases:
        assert is_closed(pts), kind
        assert classify_closed(pts) == kind


def test_open_strokes_are_not_shapes():
    rng = random.Random(3)
    assert not is_closed(line((0, 0), (200, 0), rng))
    assert not is_closed(line((0, 0), (200, 80), rng, bend=40))
    assert not is_closed(ellipse(100, 100, 50, 50, rng, sweep=0.7))  # «C» — не замкнута
    assert not is_closed(scribble(0, 0))


# ---------------------------------------------------------------- стрелки и привязка
def test_arrow_between_shapes_binds_both_ends_and_follows_them():
    rng = random.Random(1)
    b = Board()
    a, c = two_boxes(b, rng)
    r = b.add_stroke(line((150, 70), (390, 72), rng, bend=12))   # из середины одной — в другую
    assert r["kind"] == "edge"
    e = b.edges[r["id"]]
    assert (e.a, e.b) == (a, c)
    assert f"n{a} --> n{c}" in to_mermaid(b)
    # стрелка начинается и кончается на краях, а не в середине фигур
    path = b.edge_path(e)
    assert abs(path[0][0] - 180) < 6 and abs(path[-1][0] - 360) < 6
    # двигаем фигуру — стрелка тянется за ней
    b.move({("shape", c)}, 60, 200)
    path = b.edge_path(e)
    s = b.shapes[c]
    assert math.dist(path[-1], (s.x + e.ub[0] * s.w, s.y + e.ub[1] * s.h)) < 0.5
    assert s.contains(path[-1], 2) and not s.contains(path[-1], -3)
    assert f"n{a} --> n{c}" in to_mermaid(b)


def test_direction_is_how_the_line_was_drawn():
    rng = random.Random(2)
    b = Board()
    a, c = two_boxes(b, rng)
    b.add_stroke(line((420, 70), (100, 70), rng))
    assert f"n{c} --> n{a}" in to_mermaid(b)


def test_arrowhead_in_the_same_stroke_is_cut_off():
    rng = random.Random(4)
    b = Board()
    a, c = two_boxes(b, rng)
    stroke = line((150, 70), (372, 70), rng) + polyline([(372, 70), (360, 62)], 2)
    r = b.add_stroke(stroke)
    e = b.edges[r["id"]]
    assert e.explicit_b and (e.a, e.b) == (a, c)
    assert not b.ink


def test_separate_arrowheads_set_direction():
    rng = random.Random(5)
    b = Board()
    a, c = two_boxes(b, rng)
    e = b.edges[b.add_stroke(line((150, 70), (390, 70), rng))["id"]]
    end = b.edge_path(e)[-1]
    assert b.add_stroke(vee(end, (1, 0)))["kind"] == "arrowhead"
    assert e.head_b and e.explicit_b and not b.ink
    start = b.edge_path(e)[0]
    assert b.add_stroke(vee(start, (-1, 0)))["kind"] == "arrowhead"
    assert f"n{a} <--> n{c}" in to_mermaid(b)


def test_arrowhead_only_at_start_reverses_the_arrow():
    rng = random.Random(6)
    b = Board()
    a, c = two_boxes(b, rng)
    e = b.edges[b.add_stroke(line((150, 70), (390, 70), rng))["id"]]
    b.add_stroke(vee(b.edge_path(e)[0], (-1, 0)))
    assert f"n{c} --> n{a}" in to_mermaid(b)


def test_writing_near_arrow_end_inside_node_is_a_label_not_a_head():
    rng = random.Random(7)
    b = Board()
    a, c = two_boxes(b, rng)
    b.add_stroke(line((150, 70), (390, 70), rng))
    r = b.add_stroke(scribble(366, 60, w=20, h=12))
    assert r["kind"] == "hand" and r["id"] == c


def test_dangling_arrow_waits_for_a_shape():
    rng = random.Random(8)
    b = Board()
    a = b.add_stroke(rect(40, 40, 140, 60, rng))["id"]
    e = b.edges[b.add_stroke(line((150, 70), (330, 160), rng))["id"]]
    assert e.a == a and e.b is None
    assert "ни к чему не привязана" in to_mermaid(b)
    assert b.stats()["dangling"] == 1
    c = b.add_stroke(rect(300, 130, 150, 70, rng))["id"]           # рисуем фигуру вокруг конца
    assert e.b == c
    assert f"n{a} --> n{c}" in to_mermaid(b)


def test_straight_line_in_empty_space_becomes_waiting_arrow():
    rng = random.Random(9)
    b = Board()
    e = b.edges[b.add_stroke(line((100, 300), (300, 300), rng))["id"]]
    assert e.a is None and e.b is None
    a = b.add_stroke(rect(30, 270, 90, 60, rng))["id"]
    c = b.add_stroke(rect(280, 270, 90, 60, rng))["id"]
    assert (e.a, e.b) == (a, c)


def test_erasing_a_shape_keeps_arrows_and_new_shape_takes_them():
    rng = random.Random(10)
    b = Board()
    a, c = two_boxes(b, rng)
    e = b.edges[b.add_stroke(line((150, 70), (390, 70), rng))["id"]]
    assert b.erase((500, 70), 8)                                # по контуру правой фигуры
    assert c not in b.shapes and e.b is None and e.id in b.edges
    c2 = b.add_stroke(rect(350, 30, 160, 80, rng))["id"]
    assert e.b == c2


def test_self_loop_and_spill_over_border():
    rng = random.Random(11)
    b = Board()
    a = b.add_stroke(rect(100, 100, 140, 60, rng))["id"]
    loop = polyline([(220, 110), (290, 60), (300, 120), (235, 145)])
    r = b.add_stroke(wobble(loop, rng, 0.5))
    assert r["kind"] == "edge" and f"n{a} --> n{a}" in to_mermaid(b)
    # подпись чуть вылезла за край — всё равно подпись
    r = b.add_stroke(scribble(110, 140, w=30, h=26))
    assert r["kind"] == "hand" and r["id"] == a


# ---------------------------------------------------------------- подписи
def test_typed_and_handwritten_labels():
    rng = random.Random(12)
    b = Board()
    a, c = two_boxes(b, rng)
    e = b.edges[b.add_stroke(line((150, 70), (390, 70), rng))["id"]]
    b.set_label("shape", a, "Просьба")
    b.add_stroke(scribble(400, 60, rng=rng))
    b.set_label("edge", e.id, "давит  срочностью")
    m = to_mermaid(b, image=True)
    assert f'n{a}["Просьба"]' in m
    assert f'n{c}["✎"]' in m
    assert f'n{a} -->|"давит срочностью"| n{c}' in m
    assert "смотри на картинке" in m and b.needs_image()
    b.set_label("shape", c, 'Согласие "да"')
    assert f'n{c}["Согласие \'да\' ✎"]' in to_mermaid(b)


def test_handwriting_near_arrow_middle_labels_the_arrow():
    rng = random.Random(13)
    b = Board()
    a, c = two_boxes(b, rng)
    e = b.edges[b.add_stroke(line((150, 70), (390, 70), rng))["id"]]
    r = b.add_stroke(scribble(255, 44, rng=rng))
    assert r["kind"] == "edge_hand" and r["id"] == e.id
    assert f'n{a} -->|"✎"| n{c}' in to_mermaid(b)
    b.move({("shape", c)}, 0, 100)                   # подпись едет с серединой стрелки
    mid = b.edge_mid(e)
    hand = b.edge_hand_abs(e)[0]
    assert math.dist(sketch.point_at(hand, 0.5), mid) < 30


def test_write_then_circle_makes_a_labelled_shape():
    rng = random.Random(14)
    b = Board()
    b.add_stroke(scribble(70, 70, rng=rng))
    b.add_stroke(scribble(100, 72, rng=rng))
    n = b.add_note(70, 100, "сервер")
    assert len(b.ink) == 2
    s = b.add_stroke(rect(50, 50, 140, 80, rng))["id"]
    assert not b.ink and len(b.shapes[s].hand) == 2
    assert n not in b.notes and b.shapes[s].label == "сервер"


def test_retrace_does_not_create_a_second_shape():
    rng = random.Random(15)
    b = Board()
    a = b.add_stroke(rect(40, 40, 140, 60, rng))["id"]
    r = b.add_stroke(rect(42, 41, 136, 58, random.Random(99)))
    assert r == {"kind": "retrace", "id": a} and len(b.shapes) == 1


# ---------------------------------------------------------------- группы
def test_shape_around_shapes_is_a_group_and_moves_them():
    rng = random.Random(16)
    b = Board()
    a = b.add_stroke(rect(60, 60, 100, 50, rng))["id"]
    c = b.add_stroke(rect(60, 160, 100, 50, rng))["id"]
    b.add_stroke(line((110, 112), (110, 158), rng))
    g = b.add_stroke(rect(30, 30, 170, 210, rng))["id"]
    b.set_label("shape", g, "Защита")
    m = to_mermaid(b)
    assert f'subgraph n{g}["Защита"]' in m
    block = m.split(f"subgraph n{g}")[1].split("end")[0]
    assert f"n{a}[" in block and f"n{c}[" in block
    assert b.stats()["groups"] == 1 and b.stats()["nodes"] == 2
    moving = b.expand({("shape", g)})
    b.move(moving, 300, 0)
    assert b.shapes[a].x > 300 and b.shapes[c].x > 300
    # нарисовали узел внутри группы — он в группе
    d = b.add_stroke(rect(350, 260, 100, 50, rng))["id"]
    assert d not in {s.id for s in b.descendants(b.shapes[g])}
    e = b.add_stroke(rect(390, 120, 60, 40, rng))
    assert e["kind"] == "shape"


def test_arrow_between_nodes_inside_group():
    rng = random.Random(17)
    b = Board()
    g = b.add_stroke(rect(20, 20, 520, 160, rng))["id"]
    a = b.add_stroke(rect(50, 70, 120, 60, rng))["id"]
    c = b.add_stroke(rect(360, 70, 120, 60, rng))["id"]
    r = b.add_stroke(line((150, 100), (390, 100), rng))
    assert r["kind"] == "edge" and f"n{a} --> n{c}" in to_mermaid(b)
    assert b.is_group(b.shapes[g])
    assert b.add_stroke(scribble(40, 30, rng=rng)) == {"kind": "hand", "id": g}


# ---------------------------------------------------------------- хранение, отличия, текст для Claude
def test_roundtrip_and_broken_data():
    rng = random.Random(18)
    b = Board()
    a, c = two_boxes(b, rng)
    b.add_stroke(line((150, 70), (390, 70), rng))
    b.add_stroke(scribble(600, 300, rng=rng))
    b.set_label("shape", a, "A")
    b.add_note(500, 400, "заметка")
    again = Board.from_dict(b.to_dict())
    assert to_mermaid(again) == to_mermaid(b)
    assert again.next_id == b.next_id
    assert Board.from_dict({"shapes": [{"id": "x"}]}).is_empty()
    assert Board.from_dict(None).is_empty()
    broken = b.to_dict()
    broken["shapes"] = broken["shapes"][1:]                      # фигура пропала — стрелка не ломается
    fixed = Board.from_dict(broken)
    assert all(e.b is None or e.b in fixed.shapes for e in fixed.edges.values())
    assert "стрелка в n" in to_mermaid(fixed) and "ниоткуда" in to_mermaid(fixed)


def test_diff_and_prompt():
    rng = random.Random(19)
    b = Board()
    a, c = two_boxes(b, rng)
    b.set_label("shape", a, "Просьба")
    b.set_label("shape", c, "Согласие")
    prev = b.copy()
    d = b.add_stroke(rect(200, 200, 120, 60, rng))["id"]
    b.set_label("shape", d, "Проверка")
    b.add_stroke(line((440, 100), (300, 230), rng))
    changes = sketch.diff(prev, b)
    assert f"добавлен n{d} «Проверка»" in changes
    assert f"связь n{c} → n{d}" in changes
    text = sketch.board_prompt(b, "вот моя схема", image=False, prev=prev)
    assert text.startswith("вот моя схема")
    assert "[доска]" in text and "```mermaid\nflowchart" in text and "Что изменилось" in text
    assert "3 узла, 1 стрелка" in text


# ---------------------------------------------------------------- схемы Claude
CLAUDE = """flowchart TD
    A[Просьба] --> B{Срочно?}
    B -->|да| C((Паника))
    B -- нет --> D([Проверка])
    D -.-> A
    C ==> E["Согласие «да»"]
    subgraph S1[Защита]
      D & F[Перезвонить] --> G
    end
    %% комментарий
    classDef bad fill:#f99
    class C bad
"""


def test_parse_claude_mermaid():
    g = parse_mermaid(CLAUDE)
    assert g["dir"] == "TD"
    n = g["nodes"]
    assert n["A"]["label"] == "Просьба" and n["B"]["kind"] == "diamond" and n["C"]["kind"] == "circle"
    assert n["D"]["kind"] == "ellipse" and n["E"]["label"] == "Согласие «да»"
    edges = {(e["a"], e["b"]): e for e in g["edges"]}
    assert edges[("B", "C")]["label"] == "да" and edges[("B", "D")]["label"] == "нет"
    assert edges[("D", "A")]["style"] == "dotted" and edges[("C", "E")]["style"] == "thick"
    assert ("D", "G") in edges and ("F", "G") in edges
    assert {"D", "F", "G"} <= set(g["groups"][0]["members"])
    assert parse_mermaid("просто код\nx = 1") is None
    assert parse_mermaid("sequenceDiagram\nA->>B: hi") is None
    assert parse_mermaid("graph LR; A-->B; B-->C")["dir"] == "LR"


def test_claude_schema_is_laid_out_and_round_trips():
    b = from_mermaid(CLAUDE)
    shapes = list(b.shapes.values())
    nodes = [s for s in shapes if not b.is_group(s)]
    assert len(nodes) == 7 and b.stats()["groups"] == 1
    for i, s in enumerate(nodes):                         # узлы не налезают друг на друга
        for t in nodes[i + 1:]:
            assert s.x + s.w <= t.x or t.x + t.w <= s.x or s.y + s.h <= t.y or t.y + t.h <= s.y
    by = {s.label: s for s in nodes}
    assert by["Просьба"].cy < by["Срочно?"].cy < by["Паника"].cy      # сверху вниз
    again = parse_mermaid(to_mermaid(b))
    labels = {(again["nodes"][e["a"]]["label"], again["nodes"][e["b"]]["label"], e["label"]) for e in again["edges"]}
    assert ("Срочно?", "Паника", "да") in labels and ("Проверка", "Просьба", "") in labels
    lr = from_mermaid("graph LR\nA --> B --> C")
    xs = sorted(lr.shapes.values(), key=lambda s: s.label)
    assert xs[0].cx < xs[1].cx < xs[2].cx


def test_cycles_do_not_break_layout():
    b = from_mermaid("graph TD\nA-->B\nB-->C\nC-->A\nC-->C")
    assert len(b.shapes) == 3 and len(b.edges) == 4
    assert to_mermaid(b).count("-->") == 4


# настоящий ответ Claude (Sonnet) — схема DNS с длинной стрелкой «есть» через пять слоёв
DNS = """flowchart TD
    A["Браузер: нужен IP для example.com"] --> B{"Кэш браузера/ОС?"}
    B -->|"есть"| Z["IP найден, подключаемся"]
    B -->|"нет"| C["Рекурсивный резолвер (провайдер, 8.8.8.8)"]
    C --> D{"Кэш резолвера?"}
    D -->|"есть"| R["Ответ браузеру"]
    D -->|"нет"| E["Корневой сервер: где .com?"]
    E --> F["TLD-сервер .com: где example.com?"]
    F --> G["Авторитетный сервер: IP example.com"]
    G --> H["Резолвер кладёт ответ в кэш"]
    H --> R
    R --> I["Браузер кэширует и подключается"]
"""


@pytest.mark.parametrize("direction", ["TD", "LR", "BT"])
def test_long_arrows_go_around_nodes(direction):
    b = from_mermaid(DNS.replace("flowchart TD", f"flowchart {direction}"))
    assert len(b.shapes) == 11 and len(b.edges) == 11
    for e in b.edges.values():
        path = b.edge_path(e)
        dense = [sketch.point_at(path, k / 40) for k in range(1, 40)]
        for s in b.shapes.values():
            if s.id not in (e.a, e.b):
                assert not any(s.contains(p, -2) for p in dense), f"стрелка {e.label!r} режет «{s.label}»"
    shapes = list(b.shapes.values())
    for i, s in enumerate(shapes):
        for t in shapes[i + 1:]:
            assert s.x + s.w <= t.x or t.x + t.w <= s.x or s.y + s.h <= t.y or t.y + t.h <= s.y
    long_edge = next(e for e in b.edges.values() if e.label == "есть" and b.shapes[e.b].label == "Ответ браузеру")
    assert len(long_edge.ink) >= 5                      # путь в обход, а не прямая
    b.move({("shape", long_edge.b)}, 40, 30)            # на доске узел двигают — обход тянется за ним
    s = b.shapes[long_edge.b]
    assert s.contains(b.edge_path(long_edge)[-1], 2)
