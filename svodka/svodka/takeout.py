"""Стартовые интересы из Google Takeout — так «подключаемся к Google».

Рекомендации YouTube и Google наружу не отдаются, а история просмотров закрыта в YouTube API
с 2016 года. Но всё это можно выгрузить самому: takeout.google.com → «YouTube и YouTube Music»
(история, подписки) и «Мои действия → Поиск». Приложение читает выгрузку (папку или .zip,
в формате JSON или HTML), отправляет Claude только названия видео, каналов и поисковых запросов
за последний год и получает из них интересы: темы, героев, профиль. Это стартовые знания
(«априори»): лента начинает с них, а дальше учится на ваших действиях.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import claude_cli
from .config import PROMPTS_DIR, RUNTIME_DIR, ensure_dirs
from .util import norm_entity, parse_date

log = logging.getLogger(__name__)

PREFIXES = ("Watched ", "Вы посмотрели ", "Вы смотрели ", "Просмотрено: ", "Просмотрено ", "Searched for ",
            "Вы искали ", "Поиск: ", "Поисковый запрос: ", "Visited ", "Вы посетили ")
SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"}, "weight": {"type": "number"}},
            "required": ["name", "description", "weight"]}},
        "entities": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "weight": {"type": "number"}}, "required": ["name", "weight"]}},
        "subtopics": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "weight": {"type": "number"}}, "required": ["name", "weight"]}},
    },
    "required": ["profile", "topics", "entities", "subtopics"],
}


def _strip(title: str) -> str:
    title = htmllib.unescape(title or "").strip()
    for p in PREFIXES:
        if title.startswith(p):
            return title[len(p):].strip()
    return title


def _files(path: Path) -> list[tuple[str, bytes]]:
    """(имя, содержимое) нужных файлов из папки или zip-архива."""
    want = re.compile(r"(watch-history|search-history|MyActivity|subscriptions|подписки|история)", re.I)
    out: list[tuple[str, bytes]] = []
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if want.search(Path(name).name) and name.lower().endswith((".json", ".html", ".csv")):
                    out.append((name, z.read(name)))
    elif path.is_dir():
        for f in path.rglob("*"):
            if f.is_file() and want.search(f.name) and f.suffix.lower() in (".json", ".html", ".csv"):
                out.append((str(f), f.read_bytes()))
    elif path.is_file():
        out.append((str(path), path.read_bytes()))
    return out


def parse(path: str | Path, since_days: int = 365) -> dict:
    """{'videos': [(название, канал)], 'queries': [запрос], 'channels': [канал]}."""
    cutoff = time.time() - since_days * 86400
    videos, queries, channels = [], [], []
    for name, raw in _files(Path(path)):
        text = raw.decode("utf-8", errors="replace")
        low = name.lower()
        is_search = "search" in low or "поиск" in low
        if low.endswith(".json"):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for item in data if isinstance(data, list) else []:
                if not isinstance(item, dict):
                    continue
                ts = parse_date(item.get("time"))
                if ts and ts < cutoff:
                    continue
                title = _strip(item.get("title", ""))
                url = item.get("titleUrl", "") or ""
                if "youtube.com/watch" in url or (item.get("header") == "YouTube" and not is_search):
                    channel = ""
                    subs = item.get("subtitles") or []
                    if subs and isinstance(subs[0], dict):
                        channel = subs[0].get("name", "")
                    if title and not title.startswith("http"):
                        videos.append((title, channel))
                elif is_search or "google.com/search" in url:
                    q = parse_qs(urlsplit(url).query).get("q", [""])[0] or title
                    if q:
                        queries.append(q)
        elif low.endswith(".html"):
            for href, label in re.findall(r'<a href="([^"]+)">([^<]{2,300})</a>', text):
                href = htmllib.unescape(href)
                if "youtube.com/watch" in href:
                    videos.append((_strip(label), ""))
                elif "google.com/search" in href:
                    q = parse_qs(urlsplit(href).query).get("q", [""])[0] or _strip(label)
                    queries.append(q)
                elif "youtube.com/channel" in href and "subscri" in low:
                    channels.append(_strip(label))
        elif low.endswith(".csv"):
            for line in text.splitlines()[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and parts[2]:
                    channels.append(parts[2])
    # без повторов, свежие первыми (в выгрузке они и так сверху)
    seen, uniq_v = set(), []
    for t, ch in videos:
        if t not in seen:
            seen.add(t)
            uniq_v.append((t, ch))
    return {"videos": uniq_v, "queries": list(dict.fromkeys(queries)), "channels": list(dict.fromkeys(channels))}


def import_takeout(settings, storage, path: str, on_progress: Callable[[str], None] | None = None) -> dict:
    progress = on_progress or (lambda _m: None)
    progress("Читаю выгрузку Google…")
    data = parse(path)
    if not (data["videos"] or data["queries"] or data["channels"]):
        return {"ok": False, "message": "В выгрузке не нашлось истории YouTube, подписок или поисковых запросов."}
    payload = {
        "текущий профиль": storage.profile_text(),
        "текущие темы": [t["name"] for t in storage.topics()],
        "видео (название — канал)": [f"{t} — {c}" if c else t for t, c in data["videos"][:700]],
        "подписки на каналы": data["channels"][:300],
        "поисковые запросы": data["queries"][:400],
    }
    progress(f"Отправляю Claude названия: видео {min(700, len(data['videos']))}, "
             f"запросов {min(400, len(data['queries']))}, каналов {min(300, len(data['channels']))}")
    ensure_dirs()
    try:
        res = claude_cli.run(
            "Выведи интересы читателя из его истории YouTube и поиска.",
            ["--system-prompt-file", str(PROMPTS_DIR / "takeout.md"), "--tools", "", "--strict-mcp-config",
             "--mcp-config", '{"mcpServers":{}}', "--model", settings.get("claude.model", "sonnet"),
             "--json-schema", json.dumps(SCHEMA), "--max-turns", "3", "--no-session-persistence"],
            command=settings.get("claude.command", "claude"), stdin=json.dumps(payload, ensure_ascii=False),
            cwd=str(RUNTIME_DIR), timeout=600)
    except claude_cli.ClaudeError as exc:
        return {"ok": False, "message": exc.human()}
    out = res.structured or {}
    applied = apply(storage, out)
    storage.meta_set("takeout_at", str(time.time()))
    return {"ok": True, "message": f"Готово: героев {applied['entities']}, подтем {applied['subtopics']}, "
                                   f"предложено тем {applied['topics']}. Профиль — на подтверждение в Настройках.",
            "result": out}


def apply(storage, out: dict) -> dict:
    """Интересы → стартовые знания модели. Темы и профиль — только предложениями (решаете вы)."""
    n_e = n_s = 0
    for e in out.get("entities") or []:
        name, w = str(e.get("name", "")).strip(), float(e.get("weight", 0) or 0)
        if name and abs(w) >= 0.1:
            w = max(-1.0, min(1.0, w))
            storage.set_prior("entity:" + norm_entity(name), max(0.0, w) * 3, max(0.0, -w) * 3, "takeout")
            n_e += 1
    for s in out.get("subtopics") or []:
        name, w = str(s.get("name", "")).strip(), float(s.get("weight", 0) or 0)
        if name and abs(w) >= 0.1:
            w = max(-1.0, min(1.0, w))
            storage.set_prior("sub:" + norm_entity(name), max(0.0, w) * 3, max(0.0, -w) * 3, "takeout")
            n_s += 1
    topics = [t for t in out.get("topics") or [] if t.get("name")]
    if topics:
        storage.meta_set("topic_suggestions", json.dumps(topics, ensure_ascii=False))
    if (out.get("profile") or "").strip():
        storage.propose_profile(out["profile"].strip(), note="из истории YouTube и поиска Google")
    return {"entities": n_e, "subtopics": n_s, "topics": len(topics)}
