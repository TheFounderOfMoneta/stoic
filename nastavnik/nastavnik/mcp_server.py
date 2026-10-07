"""MCP-сервер «nastavnik» — единственный вход Claude-репетитора в данные приложения.

Claude не выполняет команды и не трогает файлы: он видит только эти инструменты. Сервер
проверяет всё, что Claude записывает (понятие должно быть на карте темы, оценка 1–4, фаза из
списка), а время ответа и уверенность берёт из того, что записало окно, — их Claude не знает.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .config import DB_FILE, Settings, setup_logging
from .learn import bandit, engine
from .storage import Storage
from .util import now

try:                                    # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

log = logging.getLogger(__name__)

PHASES = ("pretest", "practice", "recall")
STEPS = ("hook", "core", "practice", "recall", "gift", "loop")


class ConceptIn(BaseModel):
    slug: str = Field(description="Короткий ключ латиницей через дефис")
    title: str = Field(description="Название понятия по-русски, 1–5 слов")
    summary: str = Field("", description="Суть в одной фразе")
    kind: str = Field("concept", description="concept — знание/понимание, procedure — навык/порядок действий")
    prereqs: list[str] = Field(default_factory=list, description="Ключи понятий, без которых это не понять")


class ItemIn(BaseModel):
    prompt: str = Field(description="Вопрос, понятный без контекста разговора")
    answer: str = Field("", description="Ответ — до двух предложений (для schema — узлы и связи)")
    kind: str = Field("card", description="card — вопрос-ответ, task — маленькая задача, schema — схема по памяти")


def _j(data) -> str:
    return json.dumps(data, ensure_ascii=False)


class Service:
    """Логика инструментов (отдельно от MCP — чтобы проверять тестами напрямую)."""

    def __init__(self, storage: Storage, settings, session_id: int, topic_id: int):
        self.storage = storage
        self.settings = settings
        self.session_id = session_id
        self.topic_id = topic_id

    def _concept(self, key: str) -> dict | None:
        return self.storage.concept_by_slug(self.topic_id, key)

    def get_state(self) -> str:
        t = self.storage.topic(self.topic_id) or {}
        s = self.storage.session(self.session_id) or {}
        concepts = [{"slug": c["slug"], "title": c["title"], "kind": c["kind"], "status": c["status"],
                     "summary": c["summary"], "prereqs": c["prereqs"]} for c in self.storage.concepts(self.topic_id)]
        tries = self.storage.attempts(session_id=self.session_id)
        arms = s.get("arms") or {}
        return _j({
            "тема": t.get("title", ""), "цель": t.get("goal", ""), "уровень": t.get("level", ""),
            "интересы": self.settings.get("profile.interests", "") if self.settings else "",
            "карта": concepts,
            "план": s.get("plan") or {},
            "форматы": {e: bandit.arm_label(e, a) for e, a in arms.items() if e in bandit.EXPERIMENTS},
            "этап": s.get("step", ""),
            "в этой сессии": {"ответов": len(tries), "верно": sum(a["correct"] for a in tries),
                              "минут": round((now() - float(s.get("started") or now())) / 60)},
            "прошлые остановки": [{"прошли": c["covered"], "трудно": c["difficulties"], "петля": c["open_loop"]}
                                  for c in self.storage.checkpoints(self.topic_id, 3)],
        })

    def add_concepts(self, concepts: list[dict]) -> str:
        clean = [c for c in concepts if (c.get("title") or "").strip()][:20]
        ids = self.storage.add_concepts(self.topic_id, clean)
        return _j({"добавлено": len(ids), "ключи": [self.storage.concept(i)["slug"] for i in ids]})

    def mark_concept(self, concept: str) -> str:
        c = self._concept(concept)
        if not c:
            return _j({"ошибка": f"понятия «{concept}» нет на карте — сначала add_concepts"})
        engine.introduce_concept(self.storage, c["id"], self.session_id)
        return _j({"ок": True, "понятие": c["title"]})

    def record_attempt(self, concept: str, phase: str, correct: bool, grade: int, note: str = "") -> str:
        c = self._concept(concept)
        if phase not in PHASES:
            phase = "practice"
        grade = max(1, min(4, int(grade)))
        turn = self.storage.last_turn(self.session_id)
        self.storage.log_attempt(phase, bool(correct), grade=grade, session_id=self.session_id,
                                 topic_id=self.topic_id, concept_id=c["id"] if c else None,
                                 latency_ms=int(turn.get("latency_ms") or 0), confidence=turn.get("confidence"),
                                 note=note)
        if c and c["status"] == "new":
            engine.introduce_concept(self.storage, c["id"], self.session_id)
        tries = self.storage.attempts(session_id=self.session_id)
        recent = tries[-3:]
        hint = ""
        if len(recent) == 3 and all(a["grade"] >= 3 for a in recent):
            hint = "три верных подряд — можно усложнить"
        elif len(recent) >= 2 and not any(a["correct"] for a in recent[-2:]):
            hint = "две ошибки подряд — упрости или покажи разобранный пример"
        return _j({"записано": True, "верно в сессии": f"{sum(a['correct'] for a in tries)}/{len(tries)}",
                   "подсказка": hint})

    def add_items(self, concept: str, items: list[dict]) -> str:
        c = self._concept(concept)
        if not c:
            return _j({"ошибка": f"понятия «{concept}» нет на карте"})
        ids = engine.add_session_items(self.storage, self.topic_id, c["id"], items[:6], self.session_id)
        due = [self.storage.item(i)["due"] for i in ids]
        days = round((min(due) - now()) / 86400, 1) if due else None
        return _j({"карточек": len(ids), "первое повторение через дней": days})

    def set_step(self, step: str) -> str:
        if step not in STEPS:
            return _j({"ошибка": f"шаг должен быть одним из: {', '.join(STEPS)}"})
        self.storage.update_session(self.session_id, step=step)
        return _j({"ок": True})

    def checkpoint(self, covered: str, difficulties: str = "", open_loop: str = "", now_can: str = "") -> str:
        self.storage.add_checkpoint(self.topic_id, self.session_id, covered, difficulties, open_loop, now_can)
        return _j({"сохранено": True})


def build_server(service: Service) -> MCPServer:
    server = MCPServer("nastavnik", instructions=(
        "Инструменты приложения «Наставник»: состояние темы, отметки понятий, записи ответов, карточки для "
        "повторений, этап сессии и остановка. Статистику считает приложение."))

    @server.tool()
    def get_state() -> str:
        """Карта темы со статусами, план и форматы этой сессии, прошлые остановки, ответы в этой сессии."""
        return service.get_state()

    @server.tool()
    def add_concepts(concepts: list[ConceptIn]) -> str:
        """Добавить на карту темы новые понятия (или уточнить существующие по ключу)."""
        return service.add_concepts([c.model_dump() for c in concepts])

    @server.tool()
    def mark_concept(concept: str) -> str:
        """Отметить, что начали разбирать понятие (ключ или название с карты)."""
        return service.mark_concept(concept)

    @server.tool()
    def record_attempt(concept: str, phase: str, correct: bool, grade: int, note: str = "") -> str:
        """Записать ответ человека. phase: pretest | practice | recall. grade: 1 не смог, 2 с подсказкой
        или частично, 3 верно, 4 легко и быстро. Время ответа и уверенность приложение добавит само."""
        return service.record_attempt(concept, phase, correct, grade, note)

    @server.tool()
    def add_items(concept: str, items: list[ItemIn]) -> str:
        """Карточки для интервальных повторений по отработанному понятию (2–4 штуки)."""
        return service.add_items(concept, [i.model_dump() for i in items])

    @server.tool()
    def set_step(step: str) -> str:
        """Текущий шаг сессии: hook | core | practice | recall | gift | loop."""
        return service.set_step(step)

    @server.tool()
    def checkpoint(covered: str, difficulties: str = "", open_loop: str = "", now_can: str = "") -> str:
        """Где остановились: что прошли, что далось трудно, открытая петля на завтра, «теперь ты можешь…»."""
        return service.checkpoint(covered, difficulties, open_loop, now_can)

    return server


def main() -> None:
    setup_logging()
    db = Path(os.environ.get("NASTAVNIK_DB") or DB_FILE)
    storage = Storage(db)
    service = Service(storage, Settings(), int(os.environ.get("NASTAVNIK_SESSION") or 0),
                      int(os.environ.get("NASTAVNIK_TOPIC") or 0))
    build_server(service).run("stdio")
