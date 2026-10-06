"""Еженедельный разбор: Claude смотрит, что вы реально читали, и предлагает правки профиля и тем.

Ничего не меняется без вас: новый профиль и изменения тем приходят предложением
(«было → стало») в Настройках. Заодно раз в неделю веса сигналов подстраиваются по опросам.
"""
from __future__ import annotations

import json
import logging
import time

from . import claude_cli
from .config import PROMPTS_DIR, RUNTIME_DIR, ensure_dirs
from .rank import calibrate
from .rank.brief import build_brief
from .rank.model import InterestModel

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "profile": {"type": "string"},
        "topic_changes": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "weight": {"type": "number"}, "description": {"type": "string"},
            "reason": {"type": "string"}}, "required": ["name", "weight", "reason"]}},
        "explore_ideas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "profile", "topic_changes", "explore_ideas"],
}
MIN_INTERACTIONS = 15


def due(storage, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    last = float(storage.meta_get("weekly_at", "0") or 0)
    if now - last < 7 * 86400:
        return False
    created = float(storage.meta_get("created_at", "0") or 0)
    return now - created >= 6 * 86400 and storage.interactions_count() >= MIN_INTERACTIONS


def run(settings, storage, force: bool = False) -> dict:
    if not force and not due(storage):
        return {"ok": False, "message": "Разбор ещё рано."}
    storage.meta_set("weekly_at", str(time.time()))
    cal = None
    try:
        cal = calibrate.maybe_recalibrate(storage)
    except Exception:  # noqa: BLE001
        log.exception("калибровка")
    model = InterestModel.build(storage)
    brief = build_brief(storage, settings, model=model)
    payload = {
        "профиль": brief["профиль"],
        "темы": brief["темы"],
        "что нравится (модель)": brief["что нравится"],
        "что не нравится (модель)": brief["что не нравится"],
        "зашло за неделю": brief["зашло недавно"],
        "не зашло за неделю": brief["не зашло недавно"],
        "качество ленты": calibrate.quality(storage),
    }
    ensure_dirs()
    try:
        res = claude_cli.run(
            "Сделай еженедельный разбор ленты по данным.",
            ["--system-prompt-file", str(PROMPTS_DIR / "weekly.md"), "--tools", "", "--strict-mcp-config",
             "--mcp-config", '{"mcpServers":{}}', "--model", settings.get("claude.model", "sonnet"),
             "--json-schema", json.dumps(SCHEMA), "--max-turns", "3", "--no-session-persistence"],
            command=settings.get("claude.command", "claude"), stdin=json.dumps(payload, ensure_ascii=False),
            cwd=str(RUNTIME_DIR), timeout=600)
    except claude_cli.ClaudeError as exc:
        return {"ok": False, "message": exc.human()}
    out = res.structured or {}
    profile = (out.get("profile") or "").strip()
    if profile and profile != storage.profile_text().strip():
        storage.propose_profile(profile, note=out.get("summary", ""))
    storage.meta_set("weekly_suggestions", json.dumps({
        "summary": out.get("summary", ""), "topic_changes": out.get("topic_changes") or [],
        "explore_ideas": out.get("explore_ideas") or [], "calibration": cal, "ts": time.time()},
        ensure_ascii=False))
    return {"ok": True, "message": out.get("summary") or "Разбор готов.", "result": out}
