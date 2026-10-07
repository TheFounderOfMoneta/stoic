"""Учёба с Claude: карта новой темы, сессия по шаблону, разговор по теме без бесконечного контекста.

Как устроен контекст:
- у каждой темы свой разговор Claude (`claude -p --resume <id>`), он продолжается между сессиями;
- состояние темы (карта, где остановились, что трудно, форматы) живёт в базе, а не в разговоре;
- когда разговор вырастает больше лимита (Настройки → Дополнительно), следующий ход начинает
  новый разговор и получает в начало короткое «[состояние темы]» из базы — ничего не теряется,
  а лимиты подписки тратятся меньше;
- если Claude не находит старый разговор (удалён, другой ПК), начинаем новый так же — сами.
"""
from __future__ import annotations

import json
import logging
import random
import sys
import threading
from pathlib import Path
from typing import Callable

from . import claude_cli
from .config import CLAUDE_LEARN_DIR, DB_FILE, PROJECT_ROOT, PROMPTS_DIR, RUNTIME_DIR, ensure_dirs
from .learn import bandit, metrics, planner
from .util import jdump, now

log = logging.getLogger(__name__)

STEP_LABELS = {"hook": "Крючок", "core": "Ядро", "practice": "Практика", "recall": "По памяти",
               "gift": "Подарок", "loop": "Петля"}
LEVELS = {"beginner": "новичок", "some": "немного знаю", "confident": "уверенно"}
MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"},
                "kind": {"type": "string", "enum": ["concept", "procedure"]},
                "prereqs": {"type": "array", "items": {"type": "string"}},
                "interest": {"type": "number"},
            },
            "required": ["slug", "title", "summary", "kind", "prereqs"],
        }},
        "first_question": {"type": "string"},
    },
    "required": ["concepts"],
}
NO_SESSION_MARKERS = ("no conversation found", "session not found", "could not find session")


# ---------------------------------------------------------------- карта новой темы
def build_map(storage, settings, topic_id: int, on_progress: Callable[[str], None] | None = None,
              cancel: threading.Event | None = None) -> dict:
    """Claude составляет карту понятий темы (строгий JSON по схеме). Бросает ClaudeError."""
    t = storage.topic(topic_id)
    data = {"тема": t["title"], "цель": t["goal"], "уровень": LEVELS.get(t["level"], t["level"]),
            "интересы": settings.get("profile.interests", ""), "заметки": t["notes"]}
    if on_progress:
        on_progress("Claude составляет карту темы…")
    ensure_dirs()
    res = claude_cli.run(
        "Составь карту темы по данным из stdin.",
        ["--system-prompt-file", str(PROMPTS_DIR / "new_topic.md"),
         "--model", settings.get("claude.model", "sonnet"),
         "--fallback-model", settings.get("claude.fallback_model", "haiku"),
         "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
         "--json-schema", json.dumps(MAP_SCHEMA), "--permission-mode", "dontAsk",
         "--max-turns", "3", "--no-session-persistence"],
        command=settings.get("claude.command", "claude"), stdin=json.dumps(data, ensure_ascii=False),
        on_progress=on_progress, timeout=float(settings.get("claude.timeout_s", 300)),
        cwd=str(CLAUDE_LEARN_DIR), cancel=cancel)
    out = res.structured if isinstance(res.structured, dict) else _json_from_text(res.text)
    concepts = [c for c in (out or {}).get("concepts") or [] if isinstance(c, dict) and c.get("title")]
    if not concepts:
        raise claude_cli.ClaudeError("failed", "Claude не вернул карту темы")
    storage.add_concepts(topic_id, concepts[:20])
    if out.get("first_question"):
        storage.update_topic(topic_id, notes=(t["notes"] + "\n" if t["notes"] else "")
                             + "Первый вопрос: " + out["first_question"].strip())
    return {"concepts": len(concepts), "first_question": out.get("first_question", "")}


def _json_from_text(text: str) -> dict | None:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


# ---------------------------------------------------------------- сессия
def start_session(storage, settings, topic_id: int, plan: planner.Plan | None = None,
                  rng: random.Random | None = None) -> int:
    """Новая учебная сессия: план, форматы (бандит), напоминание, после которого начали."""
    ts = now()
    plan = plan or planner.plan_today(storage, settings, topic_id, ts)
    kind = (plan.concept or {}).get("kind", "concept")
    arms = bandit.choose_session(storage, kind, float(settings.get("learn.explore_share", 0.15)), rng)
    if metrics.motivation(storage, ts)["alarm"]:
        # тяга растёт без удовольствия и пользы — убираем самую «игровую» часть: подарок только информационный
        arms["gift"] = "now_you_can"
        arms["alarm"] = True
    trigger = metrics.trigger_for(storage, ts)
    plan_data = {"kind": plan.kind, "minutes": round(plan.budget), "reviews": len(plan.reviews),
                 "concept": (plan.concept or {}).get("slug", ""), "concept_title": (plan.concept or {}).get("title", ""),
                 "open_loop": plan.open_loop, "note": plan.note}
    return storage.start_session("learn", topic_id, started=ts, arms=arms, plan=plan_data, trigger_ts=trigger,
                                 self_started=0 if trigger else 1)


def state_block(storage, settings, topic_id: int) -> str:
    """Короткое состояние темы для начала нового разговора Claude (данные, не инструкции)."""
    t = storage.topic(topic_id)
    concepts = storage.concepts(topic_id)
    by = {"mastered": [], "learning": [], "new": []}
    for c in concepts:
        by.setdefault(c["status"], []).append(c["title"])
    hard = [cp["difficulties"] for cp in storage.checkpoints(topic_id, 3) if cp["difficulties"]]
    covered = [cp["covered"] for cp in storage.checkpoints(topic_id, 3) if cp["covered"]]
    lines = [
        "[состояние темы]",
        f"Тема: {t['title']}. Цель: {t['goal'] or 'не указана'}. Уровень: {LEVELS.get(t['level'], t['level'])}.",
        f"Интересы человека: {settings.get('profile.interests', '') or 'не указаны'}.",
        f"Освоено: {', '.join(by['mastered']) or 'пока ничего'}.",
        f"В процессе: {', '.join(by['learning']) or 'нет'}.",
        f"Дальше по карте: {', '.join(by['new'][:6]) or 'карта пройдена'}.",
    ]
    if covered:
        lines.append("Недавно прошли: " + " | ".join(covered))
    if hard:
        lines.append("Давалось трудно: " + " | ".join(hard))
    if "Первый вопрос:" in (t["notes"] or "") and not covered:
        first = t["notes"].split("Первый вопрос:", 1)[1].strip()
        lines.append(f"Идея для первого крючка: {first}")
    lines.append("Карта со всеми ключами — в get_state.")
    return "\n".join(lines)


def opening_prompt(storage, settings, session_id: int) -> str:
    s = storage.session(session_id)
    p, arms = s["plan"], s["arms"]
    fmt = "; ".join(f"{bandit.EXPERIMENTS[e]['title'].lower()} — {bandit.arm_label(e, arms[e])}"
                    for e in ("hook", "order", "present", "recall", "gift") if e in arms)
    parts = [f"[приложение] Начни сессию. Время: около {p.get('minutes', 25)} минут."]
    if p.get("concept"):
        parts.append(f"Новое понятие сегодня: «{p.get('concept_title')}» (ключ {p['concept']}).")
    else:
        parts.append("Нового понятия сегодня нет: потренируй то, что в процессе, и закрепи трудное.")
    if p.get("reviews"):
        parts.append(f"Повторений по сроку: {p['reviews']} — они пройдут отдельно, в «Повторении».")
    if p.get("open_loop"):
        parts.append(f"В прошлый раз остановились на: {p['open_loop']}. Можно начать с этого крючка.")
    if p.get("kind") == "short":
        parts.append("Сегодня человеку тяжелее обычного: сессия короткая и спокойная, без нажима.")
    elif p.get("kind") == "return":
        parts.append("Вчера был пропуск: сессия-возврат на 5 минут, только самое приятное и простое.")
    parts.append(f"Форматы этой сессии: {fmt}.")
    parts.append("Начни с крючка.")
    return " ".join(parts)


CLOSING_PROMPT = ("[приложение] Время заканчивать. Если понятие отработано — сделай карточки (add_items). "
                  "Затем подарок-пик, открытая петля на следующий раз и checkpoint. Коротко.")


def mcp_config(db_path: Path, session_id: int, topic_id: int) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {"mcpServers": {"nastavnik": {
        "command": sys.executable,
        "args": ["-m", "nastavnik", "mcp"],
        "env": {"NASTAVNIK_DB": str(db_path), "NASTAVNIK_SESSION": str(session_id),
                "NASTAVNIK_TOPIC": str(topic_id), "PYTHONPATH": str(PROJECT_ROOT)},
    }}}
    path = RUNTIME_DIR / f"mcp-learn-{session_id}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def build_args(settings, mcp_path: Path, resume: str | None) -> list[str]:
    # Свой короткий системный промпт вместо большого промпта Claude Code: учёбе не нужны
    # правила работы с кодом, а лимиты подписки тратятся заметно меньше.
    args = ["--system-prompt-file", str(PROMPTS_DIR / "tutor.md"),
            "--model", settings.get("claude.model", "sonnet"),
            "--fallback-model", settings.get("claude.fallback_model", "haiku"),
            "--mcp-config", str(mcp_path), "--strict-mcp-config",
            "--tools", "", "--allowedTools", "mcp__nastavnik__*",
            "--permission-mode", "dontAsk", "--max-turns", "16",
            "--include-partial-messages"]
    if resume:
        args += ["--resume", resume]
    return args


def send(storage, settings, session_id: int, text: str, role: str = "user", confidence: int | None = None,
         on_text: Callable[[str], None] | None = None, on_progress: Callable[[str], None] | None = None,
         cancel: threading.Event | None = None, db_path: Path | None = None, store: bool = True) -> dict:
    """Один ход разговора. role: user — сообщение человека, app — команда приложения.
    store=False — повтор после сбоя: сообщение уже записано.

    Возвращает {'text', 'rotated', 'tokens'}. Бросает ClaudeError с понятной причиной."""
    ensure_dirs()
    s = storage.session(session_id)
    topic_id = s["topic_id"]
    if store:
        storage.add_message(session_id, role, text, confidence=confidence)
    t = storage.topic(topic_id)
    limit = int(settings.get("learn.context_tokens", 60000))
    resume = t["claude_session"] or None
    rotated = bool(resume) and int(t["claude_tokens"] or 0) >= limit
    if rotated:
        resume = None
        storage.log_event("context_rotated", value=int(t["claude_tokens"] or 0), meta=str(topic_id))
    mcp_path = mcp_config(db_path or DB_FILE, session_id, topic_id)
    prompt = text if role == "app" else _with_confidence(text, confidence)

    def attempt(resume_id: str | None):
        chunks: list[str] = []

        def collect(piece: str) -> None:
            chunks.append(piece)
            if on_text:
                on_text(piece)
        full = prompt if resume_id else state_block(storage, settings, topic_id) + "\n\n" + prompt
        res = claude_cli.run(full, build_args(settings, mcp_path, resume_id),
                             command=settings.get("claude.command", "claude"), on_text=collect,
                             on_progress=on_progress, timeout=float(settings.get("claude.timeout_s", 300)),
                             cwd=str(CLAUDE_LEARN_DIR), cancel=cancel)
        return res, "".join(chunks)

    try:
        res, streamed = attempt(resume)
    except claude_cli.ClaudeError as exc:
        if resume and any(m in exc.message.lower() for m in NO_SESSION_MARKERS):
            log.warning("Разговор по теме %s не найден — начинаю новый с состоянием из базы", topic_id)
            storage.log_event("context_lost", meta=str(topic_id))
            res, streamed = attempt(None)
            rotated = True
        else:
            raise
    finally:
        try:
            mcp_path.unlink()
        except OSError:
            pass
    answer = streamed.strip() or (res.text or "").strip()
    storage.add_message(session_id, "assistant", answer)
    fields = {"claude_session": res.session_id or t["claude_session"], "claude_tokens": res.context_tokens}
    if not resume or rotated:
        fields["claude_started"] = now()
    storage.update_topic(topic_id, **fields)
    storage.update_session(session_id, claude_session=res.session_id or "")
    return {"text": answer, "rotated": rotated, "tokens": res.context_tokens}


def _with_confidence(text: str, confidence: int | None) -> str:
    if not confidence:
        return text
    words = {1: "наугад", 2: "не уверен", 3: "скорее уверен", 4: "уверен"}
    return f"{text}\n\n[приложение] Уверенность человека в ответе: {confidence}/4 ({words.get(confidence, '')})."


def finish_session(storage, session_id: int, liking: int | None, active_ms: int) -> dict:
    """Закрыть сессию: оценка (награда крючку и подарку), время, итог для экрана «что ты теперь можешь»."""
    s = storage.session(session_id)
    storage.update_session(session_id, ended=now(), liking=liking, active_ms=int(active_ms))
    if liking:
        bandit.reward_session(storage, s["arms"], int(liking))
    tries = storage.attempts(session_id=session_id)
    cps = storage.query("SELECT * FROM checkpoints WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,))
    new_items = storage.one("SELECT count(*) AS n FROM items WHERE session_id=?", (session_id,))["n"]
    concepts = storage.query("SELECT title FROM concepts WHERE intro_session=?", (session_id,))
    return {"answers": len(tries), "correct": sum(a["correct"] for a in tries), "cards": new_items,
            "concepts": [c["title"] for c in concepts], "now_can": cps[0]["now_can"] if cps else "",
            "open_loop": cps[0]["open_loop"] if cps else "", "minutes": round(active_ms / 60000)}


def step_of(storage, session_id: int) -> str:
    s = storage.session(session_id)
    return (s or {}).get("step", "")


def export_json(storage) -> str:
    """Всё о вашей учёбе одним файлом (Настройки → Дополнительно)."""
    data = {}
    for table in ("topics", "concepts", "items", "attempts", "sessions", "checkpoints", "arms", "talk_notes"):
        data[table] = storage.query(f"SELECT * FROM {table}")
    return jdump(data)
