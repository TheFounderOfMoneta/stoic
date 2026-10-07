"""База «Наставника» (SQLite, WAL).

Принципы (как в «Сводке»):
- Журнал попыток, сессий и оценок — самое ценное: на нём подстраиваются повторения,
  форматы подачи и длина сессий, и его нельзя восстановить. Поэтому ежедневный бэкап.
- Повреждённая база откладывается в сторону (*.broken-…), берётся последний бэкап.
- Несколько процессов (окно, MCP-сервер, напоминание) работают с одной базой: WAL + ожидание.
- Состояние темы живёт здесь, а не в разговоре с Claude: разговор можно сжимать или
  начинать заново, ничего не теряя.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .util import day_key, jdump, jload, now, slugify

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'beginner',
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    claude_session TEXT NOT NULL DEFAULT '',
    claude_tokens INTEGER NOT NULL DEFAULT 0,
    claude_started REAL
);

CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'concept',
    prereqs TEXT NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL DEFAULT 0,
    interest REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'new',
    intro_session INTEGER,
    introduced_at REAL,
    created_at REAL NOT NULL,
    UNIQUE(topic_id, slug)
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    concept_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'card',
    prompt TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    session_id INTEGER,
    state TEXT NOT NULL DEFAULT 'new',
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    due REAL,
    last_review REAL,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    suspended INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_due ON items(due);
CREATE INDEX IF NOT EXISTS items_concept ON items(concept_id);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id INTEGER,
    topic_id INTEGER,
    concept_id INTEGER,
    item_id INTEGER,
    phase TEXT NOT NULL,
    correct INTEGER NOT NULL,
    grade INTEGER NOT NULL DEFAULT 3,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER,
    elapsed_days REAL,
    retrievability REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS attempts_ts ON attempts(ts);
CREATE INDEX IF NOT EXISTS attempts_session ON attempts(session_id);
CREATE INDEX IF NOT EXISTS attempts_item ON attempts(item_id);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    topic_id INTEGER,
    started REAL NOT NULL,
    ended REAL,
    active_ms INTEGER NOT NULL DEFAULT 0,
    self_started INTEGER NOT NULL DEFAULT 1,
    trigger_ts REAL,
    liking INTEGER,
    arms TEXT NOT NULL DEFAULT '{}',
    step TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT '{}',
    mode TEXT NOT NULL DEFAULT '',
    mood_before INTEGER,
    mood_after INTEGER,
    feeling TEXT NOT NULL DEFAULT '',
    claude_session TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence INTEGER
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id INTEGER,
    topic_id INTEGER NOT NULL,
    covered TEXT NOT NULL DEFAULT '',
    difficulties TEXT NOT NULL DEFAULT '',
    open_loop TEXT NOT NULL DEFAULT '',
    now_can TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS checkpoints_topic ON checkpoints(topic_id);

CREATE TABLE IF NOT EXISTS arms (
    experiment TEXT NOT NULL,
    context TEXT NOT NULL,
    arm TEXT NOT NULL,
    alpha REAL NOT NULL DEFAULT 1,
    beta REAL NOT NULL DEFAULT 1,
    n INTEGER NOT NULL DEFAULT 0,
    updated REAL,
    PRIMARY KEY (experiment, context, arm)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);

CREATE TABLE IF NOT EXISTS boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id INTEGER,
    topic_id INTEGER,
    item_id INTEGER,
    data TEXT NOT NULL DEFAULT '{}',
    mermaid TEXT NOT NULL DEFAULT '',
    png BLOB,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS boards_session ON boards(session_id);

CREATE TABLE IF NOT EXISTS talk_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id INTEGER,
    summary TEXT NOT NULL,
    themes TEXT NOT NULL DEFAULT '[]',
    helped TEXT NOT NULL DEFAULT '',
    technique TEXT NOT NULL DEFAULT ''
);
"""

TOPIC_FIELDS = {"title", "goal", "level", "notes", "archived", "claude_session", "claude_tokens", "claude_started"}
CONCEPT_FIELDS = {"title", "summary", "kind", "prereqs", "position", "interest", "status", "intro_session",
                  "introduced_at"}
ITEM_FIELDS = {"prompt", "answer", "kind", "state", "stability", "difficulty", "due", "last_review", "reps",
               "lapses", "suspended", "concept_id"}
SESSION_FIELDS = {"ended", "active_ms", "self_started", "trigger_ts", "liking", "arms", "step", "plan", "mode",
                  "mood_before", "mood_after", "feeling", "claude_session", "note"}


def _corrupted(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("malformed", "not a database", "corrupt", "file is encrypted"))


class Storage:
    """Доступ к базе. Потокобезопасен: одно соединение под замком."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.problem = ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open()
        except sqlite3.DatabaseError as exc:
            if not _corrupted(exc):
                raise
            self._recover(exc)

    # ------------------------------------------------------------------ соединение
    def _open(self) -> None:
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.execute("SELECT count(*) FROM topics").fetchone()
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (str(now()),))
        self.db.commit()

    def _recover(self, exc: Exception) -> None:
        """База повреждена: откладываем её и поднимаем последний бэкап (или новую базу)."""
        log.error("База повреждена (%s) — восстанавливаю", exc)
        self.problem = "База была повреждена и восстановлена из резервной копии."
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                try:
                    os.replace(src, Path(f"{self.path}.broken-{stamp}{suffix}"))
                except OSError:
                    pass
        for b in sorted(self.path.parent.glob("backups/nastavnik-*.sqlite3"), reverse=True):
            try:
                src = sqlite3.connect(str(b))
                dst = sqlite3.connect(str(self.path))
                src.backup(dst)
                src.close()
                dst.close()
                self._open()
                return
            except sqlite3.DatabaseError:
                for suffix in ("", "-wal", "-shm"):
                    try:
                        Path(str(self.path) + suffix).unlink()
                    except OSError:
                        pass
        self.problem = "База была повреждена; резервной копии не нашлось — начата новая."
        self._open()

    def close(self) -> None:
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, tuple(params))
            self.db.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self.db.execute(sql, tuple(params)).fetchone()
            return dict(row) if row is not None else None

    def _update(self, table: str, allowed: set[str], row_id: int, fields: dict) -> None:
        cols = [k for k in fields if k in allowed]
        if not cols:
            return
        values = [jdump(fields[k]) if isinstance(fields[k], (list, dict)) else fields[k] for k in cols]
        self.execute(f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?", (*values, row_id))

    # ------------------------------------------------------------------ meta
    def meta_get(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value) -> None:
        self.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))

    # ------------------------------------------------------------------ темы
    def add_topic(self, title: str, goal: str = "", level: str = "beginner", notes: str = "") -> int:
        cur = self.execute("INSERT INTO topics(title, goal, level, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                           (title.strip() or "Новая тема", goal.strip(), level, notes.strip(), now()))
        return int(cur.lastrowid)

    def topic(self, topic_id: int) -> dict | None:
        return self.one("SELECT * FROM topics WHERE id=?", (topic_id,))

    def topics(self, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived=0"
        return self.query(f"SELECT * FROM topics {where} ORDER BY created_at")

    def update_topic(self, topic_id: int, **fields) -> None:
        self._update("topics", TOPIC_FIELDS, topic_id, fields)

    def delete_topic(self, topic_id: int) -> None:
        with self._lock:
            for table, col in (("concepts", "topic_id"), ("items", "topic_id"), ("checkpoints", "topic_id"),
                               ("attempts", "topic_id")):
                self.db.execute(f"DELETE FROM {table} WHERE {col}=?", (topic_id,))
            self.db.execute("DELETE FROM topics WHERE id=?", (topic_id,))
            self.db.commit()

    # ------------------------------------------------------------------ понятия
    @staticmethod
    def _concept(row: dict | None) -> dict | None:
        if row is None:
            return None
        row["prereqs"] = jload(row.get("prereqs"), [])
        return row

    def add_concepts(self, topic_id: int, concepts: list[dict]) -> list[int]:
        """Добавить или обновить понятия (по slug). Возвращает id в том же порядке."""
        ids: list[int] = []
        with self._lock:
            base = self.db.execute("SELECT coalesce(max(position), -1) + 1 FROM concepts WHERE topic_id=?",
                                   (topic_id,)).fetchone()[0]
            for k, c in enumerate(concepts):
                slug = slugify(c.get("slug") or c.get("title", ""))
                prereqs = [slugify(p) for p in (c.get("prereqs") or []) if p]
                kind = c.get("kind") if c.get("kind") in ("concept", "procedure") else "concept"
                row = self.db.execute("SELECT id FROM concepts WHERE topic_id=? AND slug=?", (topic_id, slug)).fetchone()
                if row:
                    self.db.execute("UPDATE concepts SET title=?, summary=?, kind=?, prereqs=? WHERE id=?",
                                    (c.get("title") or slug, c.get("summary", ""), kind, jdump(prereqs), row[0]))
                    ids.append(int(row[0]))
                else:
                    cur = self.db.execute(
                        "INSERT INTO concepts(topic_id, slug, title, summary, kind, prereqs, position, interest, "
                        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (topic_id, slug, c.get("title") or slug, c.get("summary", ""), kind, jdump(prereqs),
                         base + k, float(c.get("interest", 0.5) or 0.5), now()))
                    ids.append(int(cur.lastrowid))
            self.db.commit()
        return ids

    def concepts(self, topic_id: int) -> list[dict]:
        return [self._concept(r) for r in self.query("SELECT * FROM concepts WHERE topic_id=? ORDER BY position",
                                                       (topic_id,))]

    def concept(self, concept_id: int) -> dict | None:
        return self._concept(self.one("SELECT * FROM concepts WHERE id=?", (concept_id,)))

    def concept_by_slug(self, topic_id: int, slug_or_title: str) -> dict | None:
        row = self.one("SELECT * FROM concepts WHERE topic_id=? AND slug=?", (topic_id, slugify(slug_or_title)))
        if row is None:
            row = self.one("SELECT * FROM concepts WHERE topic_id=? AND lower(title)=lower(?)",
                           (topic_id, (slug_or_title or "").strip()))
        return self._concept(row)

    def update_concept(self, concept_id: int, **fields) -> None:
        self._update("concepts", CONCEPT_FIELDS, concept_id, fields)

    # ------------------------------------------------------------------ карточки
    def add_item(self, topic_id: int, concept_id: int | None, prompt: str, answer: str = "", kind: str = "card",
                 session_id: int | None = None, due: float | None = None) -> int:
        cur = self.execute(
            "INSERT INTO items(topic_id, concept_id, kind, prompt, answer, created_at, session_id, due) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (topic_id, concept_id, kind if kind in ("card", "task", "schema") else "card", prompt.strip(),
             answer.strip(), now(), session_id, due))
        return int(cur.lastrowid)

    def item(self, item_id: int) -> dict | None:
        return self.one("SELECT * FROM items WHERE id=?", (item_id,))

    def items(self, topic_id: int | None = None, concept_id: int | None = None) -> list[dict]:
        if concept_id is not None:
            return self.query("SELECT * FROM items WHERE concept_id=? ORDER BY id", (concept_id,))
        if topic_id is not None:
            return self.query("SELECT * FROM items WHERE topic_id=? ORDER BY id", (topic_id,))
        return self.query("SELECT * FROM items ORDER BY id")

    def update_item(self, item_id: int, **fields) -> None:
        self._update("items", ITEM_FIELDS, item_id, fields)

    def due_items(self, until: float, topic_id: int | None = None, limit: int = 500) -> list[dict]:
        sql = ("SELECT i.* FROM items i JOIN topics t ON t.id=i.topic_id WHERE t.archived=0 AND i.suspended=0 "
               "AND i.due IS NOT NULL AND i.due<=?")
        params: list = [until]
        if topic_id is not None:
            sql += " AND i.topic_id=?"
            params.append(topic_id)
        return self.query(sql + " ORDER BY i.due LIMIT ?", (*params, limit))

    # ------------------------------------------------------------------ попытки
    def log_attempt(self, phase: str, correct: bool, grade: int = 3, session_id: int | None = None,
                    topic_id: int | None = None, concept_id: int | None = None, item_id: int | None = None,
                    latency_ms: int = 0, confidence: int | None = None, elapsed_days: float | None = None,
                    retrievability: float | None = None, note: str = "", ts: float | None = None) -> int:
        cur = self.execute(
            "INSERT INTO attempts(ts, session_id, topic_id, concept_id, item_id, phase, correct, grade, latency_ms, "
            "confidence, elapsed_days, retrievability, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts or now(), session_id, topic_id, concept_id, item_id, phase, int(bool(correct)), int(grade),
             int(max(0, latency_ms)), confidence, elapsed_days, retrievability, note[:500]))
        return int(cur.lastrowid)

    def attempts(self, since: float = 0.0, session_id: int | None = None) -> list[dict]:
        if session_id is not None:
            return self.query("SELECT * FROM attempts WHERE session_id=? ORDER BY ts", (session_id,))
        return self.query("SELECT * FROM attempts WHERE ts>=? ORDER BY ts", (since,))

    # ------------------------------------------------------------------ сессии
    def start_session(self, kind: str, topic_id: int | None = None, **fields) -> int:
        cur = self.execute("INSERT INTO sessions(kind, topic_id, started) VALUES (?, ?, ?)",
                           (kind, topic_id, fields.pop("started", None) or now()))
        sid = int(cur.lastrowid)
        if fields:
            self.update_session(sid, **fields)
        return sid

    def update_session(self, session_id: int, **fields) -> None:
        self._update("sessions", SESSION_FIELDS, session_id, fields)

    def session(self, session_id: int) -> dict | None:
        row = self.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if row:
            row["arms"] = jload(row.get("arms"), {})
            row["plan"] = jload(row.get("plan"), {})
        return row

    def sessions(self, kind: str | None = None, since: float = 0.0, finished: bool = False) -> list[dict]:
        sql = "SELECT * FROM sessions WHERE started>=?"
        params: list = [since]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if finished:
            sql += " AND ended IS NOT NULL"
        rows = self.query(sql + " ORDER BY started", params)
        for r in rows:
            r["arms"] = jload(r.get("arms"), {})
            r["plan"] = jload(r.get("plan"), {})
        return rows

    def delete_session(self, session_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self.db.execute("DELETE FROM boards WHERE session_id=?", (session_id,))
            self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.db.commit()

    # ------------------------------------------------------------------ сообщения
    def add_message(self, session_id: int, role: str, text: str, confidence: int | None = None,
                    ts: float | None = None) -> int:
        cur = self.execute("INSERT INTO messages(session_id, ts, role, text, confidence) VALUES (?, ?, ?, ?, ?)",
                           (session_id, ts or now(), role, text, confidence))
        return int(cur.lastrowid)

    def messages(self, session_id: int) -> list[dict]:
        return self.query("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))

    def delete_messages(self, session_id: int) -> None:
        self.execute("DELETE FROM messages WHERE session_id=?", (session_id,))

    def last_turn(self, session_id: int) -> dict:
        """Последний ответ человека и время перед ним: из них считается время ответа на вопрос Claude."""
        user = self.one("SELECT * FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                        (session_id,))
        if not user:
            return {}
        prev = self.one("SELECT * FROM messages WHERE session_id=? AND role='assistant' AND id<? "
                        "ORDER BY id DESC LIMIT 1", (session_id, user["id"]))
        latency = int((user["ts"] - prev["ts"]) * 1000) if prev else 0
        return {"text": user["text"], "confidence": user["confidence"], "latency_ms": max(0, latency),
                "ts": user["ts"]}

    # ------------------------------------------------------------------ доски
    def save_board(self, data: dict, mermaid: str = "", png: bytes | None = None, session_id: int | None = None,
                   topic_id: int | None = None, item_id: int | None = None, sent: bool = False) -> int:
        """Схема с доски. Черновик (sent=0) у сессии один — перезаписывается; отправленные копятся."""
        with self._lock:
            if not sent and session_id is not None:
                self.db.execute("DELETE FROM boards WHERE session_id=? AND sent=0", (session_id,))
            cur = self.db.execute(
                "INSERT INTO boards(ts, session_id, topic_id, item_id, data, mermaid, png, sent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), session_id, topic_id, item_id, jdump(data), mermaid, png, int(sent)))
            self.db.commit()
            return int(cur.lastrowid)

    def boards(self, session_id: int | None = None, item_id: int | None = None, sent: bool | None = None) -> list[dict]:
        sql, args = "SELECT * FROM boards WHERE 1=1", []
        if session_id is not None:
            sql += " AND session_id=?"
            args.append(session_id)
        if item_id is not None:
            sql += " AND item_id=?"
            args.append(item_id)
        if sent is not None:
            sql += " AND sent=?"
            args.append(int(sent))
        rows = self.query(sql + " ORDER BY id", args)
        for r in rows:
            r["data"] = jload(r["data"], {})
        return rows

    def last_board(self, session_id: int, sent: bool | None = None) -> dict | None:
        rows = self.boards(session_id=session_id, sent=sent)
        return rows[-1] if rows else None

    # ------------------------------------------------------------------ чекпоинты
    def add_checkpoint(self, topic_id: int, session_id: int | None, covered: str = "", difficulties: str = "",
                       open_loop: str = "", now_can: str = "") -> int:
        cur = self.execute(
            "INSERT INTO checkpoints(ts, session_id, topic_id, covered, difficulties, open_loop, now_can) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now(), session_id, topic_id, covered.strip(), difficulties.strip(), open_loop.strip(), now_can.strip()))
        return int(cur.lastrowid)

    def checkpoints(self, topic_id: int, limit: int = 3) -> list[dict]:
        return self.query("SELECT * FROM checkpoints WHERE topic_id=? ORDER BY id DESC LIMIT ?", (topic_id, limit))

    def last_checkpoint(self) -> dict | None:
        return self.one("SELECT c.*, t.title AS topic_title FROM checkpoints c JOIN topics t ON t.id=c.topic_id "
                        "WHERE t.archived=0 ORDER BY c.id DESC LIMIT 1")

    # ------------------------------------------------------------------ бандиты
    def arm_stats(self, experiment: str, context: str) -> dict[str, dict]:
        rows = self.query("SELECT arm, alpha, beta, n FROM arms WHERE experiment=? AND context=?",
                          (experiment, context))
        return {r["arm"]: r for r in rows}

    def update_arm(self, experiment: str, context: str, arm: str, reward: float, weight: float = 1.0) -> None:
        """Наблюдение для бандита: успех reward (0–1) с весом weight (карточки одного понятия делят вес 1)."""
        reward = max(0.0, min(1.0, float(reward)))
        w = max(0.0, float(weight))
        self.execute(
            "INSERT INTO arms(experiment, context, arm, alpha, beta, n, updated) VALUES (?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(experiment, context, arm) DO UPDATE SET alpha=alpha+?, beta=beta+?, n=n+1, updated=?",
            (experiment, context, arm, 1 + w * reward, 1 + w * (1 - reward), now(), w * reward, w * (1 - reward),
             now()))

    def reset_arms(self) -> None:
        self.execute("DELETE FROM arms")

    # ------------------------------------------------------------------ события
    def log_event(self, kind: str, value: float = 0.0, meta: str = "", ts: float | None = None) -> None:
        self.execute("INSERT INTO events(ts, kind, value, meta) VALUES (?, ?, ?, ?)", (ts or now(), kind, value, meta))

    def events(self, kind: str, since: float = 0.0) -> list[dict]:
        return self.query("SELECT * FROM events WHERE kind=? AND ts>=? ORDER BY ts", (kind, since))

    # ------------------------------------------------------------------ разговор
    def add_talk_note(self, session_id: int | None, summary: str, themes: list[str], helped: str = "",
                      technique: str = "") -> int:
        cur = self.execute("INSERT INTO talk_notes(ts, session_id, summary, themes, helped, technique) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (now(), session_id, summary.strip(), jdump([t for t in themes if t][:8]), helped.strip(),
                            technique.strip()))
        return int(cur.lastrowid)

    def talk_notes(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM talk_notes ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["themes"] = jload(r.get("themes"), [])
        return rows

    def delete_talk_note(self, note_id: int) -> None:
        self.execute("DELETE FROM talk_notes WHERE id=?", (note_id,))

    # ------------------------------------------------------------------ обслуживание
    def backup(self, directory: Path, keep: int = 3) -> Path | None:
        """Копия базы раз в сутки (SQLite backup API — безопасно при работающей базе)."""
        if keep <= 0:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"nastavnik-{day_key(now())}.sqlite3"
        if target.exists():
            return target
        try:
            dst = sqlite3.connect(str(target))
            with self._lock:
                self.db.backup(dst)
            dst.close()
        except (sqlite3.Error, OSError) as exc:
            log.warning("Бэкап не удался: %s", exc)
            return None
        for old in sorted(directory.glob("nastavnik-*.sqlite3"), reverse=True)[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        return target
