"""База «Сводки» (SQLite, WAL).

Принципы:
- Журнал действий (показы, чтение, реакции, опросы) — самое ценное: на нём учатся
  рекомендации, и его нельзя восстановить. Поэтому ежедневный бэкап, а старые статьи
  при очистке не удаляются целиком: остаётся «скелет» (тема, герои, источник), чтобы
  долгая память интересов не теряла историю.
- Повреждённая база откладывается в сторону (*.broken-…), берётся последний бэкап.
- Несколько процессов (окно, сбор, MCP-сервер) работают с одной базой: WAL + ожидание.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .util import day_key, domain_of, jdump, jload, normalize_url, norm_entity, now

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    url_key TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT '',
    title_ru TEXT NOT NULL DEFAULT '',
    title_orig TEXT NOT NULL DEFAULT '',
    summary_ru TEXT NOT NULL DEFAULT '[]',
    why_ru TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    subtopic TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL DEFAULT 'core',
    importance REAL NOT NULL DEFAULT 0.5,
    fit REAL NOT NULL DEFAULT 0.5,
    words INTEGER NOT NULL DEFAULT 0,
    story_key TEXT NOT NULL DEFAULT '',
    published_at REAL,
    collected_at REAL NOT NULL,
    run_id INTEGER,
    query TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    saved INTEGER NOT NULL DEFAULT 0,
    followed INTEGER NOT NULL DEFAULT 0,
    extract_status TEXT NOT NULL DEFAULT 'pending',
    translate_status TEXT NOT NULL DEFAULT 'none',
    read_pos REAL NOT NULL DEFAULT 0,
    purged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS articles_collected ON articles(collected_at);
CREATE INDEX IF NOT EXISTS articles_story ON articles(story_key);

CREATE TABLE IF NOT EXISTS blocks (
    article_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    text_orig TEXT NOT NULL DEFAULT '',
    text_ru TEXT,
    src TEXT,
    PRIMARY KEY (article_id, idx)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    article_id INTEGER,
    kind TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_article ON events(article_id);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    article_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    surface TEXT NOT NULL DEFAULT 'feed',
    visible_ms INTEGER NOT NULL DEFAULT 0,
    randomized INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS impressions_article ON impressions(article_id);

CREATE TABLE IF NOT EXISTS reads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    article_id INTEGER NOT NULL,
    active_ms INTEGER NOT NULL DEFAULT 0,
    scroll_pct REAL NOT NULL DEFAULT 0,
    expected_ms INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'ru'
);
CREATE INDEX IF NOT EXISTS reads_article ON reads(article_id);

CREATE TABLE IF NOT EXISTS surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    article_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    include_words TEXT NOT NULL DEFAULT '[]',
    exclude_words TEXT NOT NULL DEFAULT '[]',
    weight REAL NOT NULL DEFAULT 1.0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    until REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS priors (
    feature TEXT PRIMARY KEY,
    pos REAL NOT NULL DEFAULT 0,
    neg REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started REAL NOT NULL,
    finished REAL,
    status TEXT NOT NULL DEFAULT 'running',
    found INTEGER NOT NULL DEFAULT 0,
    saved INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS seen_urls (
    url_key TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (url_key, run_id)
);

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_checked REAL,
    saved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS glossary (
    term_orig TEXT PRIMARY KEY,
    term_ru TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    data TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(stems, tokenize='unicode61 remove_diacritics 2');
"""

# Стартовые интересы владельца (из разговора при проектировании). Меняются в Настройках.
SEED_TOPICS = [
    ("ИИ: развитие",
     "Как развивается искусственный интеллект: новые модели, агенты, исследования, инфраструктура, "
     "реальные применения. Важны системные сдвиги, а не хайп.", 1.0),
    ("Новые технологии",
     "Реальные новые технологии, которые меняют то, как устроены системы: вычисления, энергетика, связь, "
     "биотех, робототехника. Работающие решения, а не обещания.", 1.0),
    ("Китай: внутренняя политика",
     "Как устроено управление Китаем изнутри: институты, реформы, экономическая и технологическая политика, "
     "механизмы принятия решений.", 1.0),
    ("США: внутренняя политика",
     "Как устроено управление США изнутри: институты, законы, регулирование (в том числе ИИ и технологий), "
     "бюрократия, механизмы принятия решений.", 1.0),
    ("Россия: внутренняя политика",
     "Как устроено управление Россией изнутри: институты, реформы, экономика, регулирование технологий, "
     "механизмы принятия решений.", 1.0),
    ("Внешняя политика Китая, США и России",
     "Отношения между странами и их стратегия — интересно, но меньше, чем внутреннее устройство.", 0.5),
]

SEED_PROFILE = (
    "Люблю выстраивать системы и разбираться в системном: как устроены институты, процессы, механизмы "
    "управления и принятия решений. ИИ интересен и как быстро развивающаяся технология, и как система. "
    "Нужны реальные новые технологии, а не хайп и обещания. В политике Китая, США и России важнее внутреннее "
    "устройство, реформы и то, как принимаются решения; внешняя политика — во вторую очередь. "
    "Предпочитаю разборы с механизмами и фактами пересказу заявлений."
)

ARTICLE_FIELDS = {
    "url", "url_key", "domain", "source", "lang", "title_ru", "title_orig", "summary_ru", "why_ru", "topic",
    "subtopic", "entities", "kind", "bucket", "importance", "fit", "words", "story_key", "published_at",
    "collected_at", "run_id", "query", "status", "saved", "followed", "extract_status", "translate_status",
    "read_pos", "purged",
}
JSON_FIELDS = {"summary_ru": [], "entities": []}


def _corrupted(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("malformed", "not a database", "corrupt", "file is encrypted"))


class Storage:
    """Доступ к базе. Потокобезопасен: одно соединение под замком."""

    def __init__(self, path: Path, seed: bool = True):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.fts_ok = True
        self.problem = ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open()
        except sqlite3.DatabaseError as exc:
            if not _corrupted(exc):
                raise
            self._recover(exc)
        if seed:
            self.seed_defaults()

    # ------------------------------------------------------------------ соединение
    def _open(self) -> None:
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executescript(SCHEMA)
        try:
            self.db.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError as exc:      # сборка SQLite без FTS5 — поиск станет проще
            log.warning("FTS5 недоступен: %s", exc)
            self.fts_ok = False
        self.db.execute("SELECT count(*) FROM articles").fetchone()
        self._migrate()
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        self.db.commit()

    def _migrate(self) -> None:
        """Добавить колонки, появившиеся в новых версиях (старые базы продолжают работать)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(impressions)")}
        if "parts" not in cols:
            self.db.execute("ALTER TABLE impressions ADD COLUMN parts TEXT NOT NULL DEFAULT ''")

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
        backups = sorted(self.path.parent.glob("backups/svodka-*.sqlite3"), reverse=True)
        for b in backups:
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

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()):
        with self._lock:
            return self.db.execute(sql, tuple(params)).fetchone()

    # ------------------------------------------------------------------ meta
    def meta_get(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))

    # ------------------------------------------------------------------ стартовые данные
    def seed_defaults(self) -> None:
        if self.meta_get("seeded"):
            return
        ts = now()
        with self._lock:
            for name, desc, weight in SEED_TOPICS:
                self.db.execute("INSERT OR IGNORE INTO topics(name, description, weight, created_at) "
                                "VALUES (?, ?, ?, ?)", (name, desc, weight, ts))
            if not self.db.execute("SELECT 1 FROM profile WHERE status='active'").fetchone():
                self.db.execute("INSERT INTO profile(ts, text, author, status, note) VALUES (?, ?, 'user', 'active', "
                                "'стартовый профиль')", (ts, SEED_PROFILE))
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seeded', '1')")
            self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (str(ts),))
            self.db.commit()

    # ------------------------------------------------------------------ статьи
    def _article_row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for key, default in JSON_FIELDS.items():
            d[key] = jload(d.get(key), default)
        return d

    def add_article(self, data: dict) -> int | None:
        """Новая статья; None — если такая ссылка уже есть."""
        item = {k: v for k, v in data.items() if k in ARTICLE_FIELDS}
        item.setdefault("url_key", normalize_url(item.get("url", "")))
        item.setdefault("domain", domain_of(item.get("url", "")))
        item.setdefault("collected_at", now())
        # Герои хранятся как написаны (для показа); признак для обучения — в нижнем регистре (rank/features).
        seen, ents = set(), []
        for e in item.get("entities") or []:
            key = norm_entity(str(e))
            if key and key not in seen:
                seen.add(key)
                ents.append(" ".join(str(e).split())[:80])
        item["entities"] = ents[:5]
        for key in JSON_FIELDS:
            if key in item and not isinstance(item[key], str):
                item[key] = jdump(item[key])
        if not item.get("url_key"):
            return None
        cols = ", ".join(item)
        marks = ", ".join("?" for _ in item)
        with self._lock:
            try:
                cur = self.db.execute(f"INSERT INTO articles({cols}) VALUES ({marks})", tuple(item.values()))
                self.db.commit()
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def update_article(self, article_id: int, **fields) -> None:
        item = {k: v for k, v in fields.items() if k in ARTICLE_FIELDS}
        if not item:
            return
        for key in JSON_FIELDS:
            if key in item and not isinstance(item[key], str):
                item[key] = jdump(item[key])
        sets = ", ".join(f"{k}=?" for k in item)
        self.execute(f"UPDATE articles SET {sets} WHERE id=?", (*item.values(), article_id))

    def article(self, article_id: int) -> dict | None:
        return self._article_row(self.one("SELECT * FROM articles WHERE id=?", (article_id,)))

    def article_by_url(self, url: str) -> dict | None:
        return self._article_row(self.one("SELECT * FROM articles WHERE url_key=?", (normalize_url(url),)))

    def articles(self, where: str = "1", params: Iterable[Any] = (), order: str = "collected_at DESC",
                 limit: int | None = None) -> list[dict]:
        sql = f"SELECT * FROM articles WHERE {where} ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._article_row(r) for r in self.query(sql, params)]

    def known_url_keys(self, keys: Iterable[str]) -> set[str]:
        keys = [k for k in keys if k]
        if not keys:
            return set()
        out: set[str] = set()
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            out |= {r["url_key"] for r in self.query(f"SELECT url_key FROM articles WHERE url_key IN ({marks})", chunk)}
        return out

    # ------------------------------------------------------------------ текст статьи
    def set_blocks(self, article_id: int, blocks: list[dict]) -> None:
        with self._lock:
            self.db.execute("DELETE FROM blocks WHERE article_id=?", (article_id,))
            self.db.executemany(
                "INSERT INTO blocks(article_id, idx, type, text_orig, text_ru, src) VALUES (?, ?, ?, ?, ?, ?)",
                [(article_id, i, b.get("type", "p"), b.get("text", ""), b.get("text_ru"), b.get("src"))
                 for i, b in enumerate(blocks)])
            self.db.commit()

    def blocks(self, article_id: int) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM blocks WHERE article_id=? ORDER BY idx", (article_id,))]

    def set_block_ru(self, article_id: int, idx: int, text: str) -> None:
        self.execute("UPDATE blocks SET text_ru=? WHERE article_id=? AND idx=?", (text, article_id, idx))

    # ------------------------------------------------------------------ журнал действий
    def log_event(self, article_id: int | None, kind: str, value: float = 0.0, meta: str = "",
                  ts: float | None = None) -> None:
        self.execute("INSERT INTO events(ts, article_id, kind, value, meta) VALUES (?, ?, ?, ?, ?)",
                     (ts if ts is not None else now(), article_id, kind, float(value), meta))

    def log_impression(self, article_id: int, position: int, visible_ms: int, surface: str = "feed",
                       randomized: bool = False, ts: float | None = None, parts: dict | None = None) -> None:
        """Показ статьи. parts — как ранжирование оценило её в этот момент (для подстройки весов)."""
        self.execute("INSERT INTO impressions(ts, article_id, position, surface, visible_ms, randomized, parts) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (ts if ts is not None else now(), article_id, int(position), surface, int(visible_ms),
                      int(bool(randomized)), jdump(parts) if parts else ""))

    def log_read(self, article_id: int, active_ms: int, scroll_pct: float, expected_ms: int, mode: str = "ru",
                 ts: float | None = None) -> None:
        self.execute("INSERT INTO reads(ts, article_id, active_ms, scroll_pct, expected_ms, mode) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (ts if ts is not None else now(), article_id, int(active_ms), float(scroll_pct),
                      int(expected_ms), mode))

    def log_survey(self, article_id: int, stars: int, reason: str = "", ts: float | None = None) -> None:
        self.execute("INSERT INTO surveys(ts, article_id, stars, reason) VALUES (?, ?, ?, ?)",
                     (ts if ts is not None else now(), article_id, int(stars), reason))

    def interactions_count(self) -> int:
        """Сколько осознанных действий накопилось (уверенность в персональной модели)."""
        row = self.one("SELECT (SELECT count(*) FROM events WHERE kind NOT IN ('open')) + "
                       "(SELECT count(*) FROM reads WHERE active_ms >= 5000) + "
                       "(SELECT count(*) FROM surveys) AS n")
        return int(row["n"] or 0)

    def surveys_today(self) -> int:
        start = time.mktime(time.strptime(day_key(now()), "%Y-%m-%d"))
        return int(self.one("SELECT count(*) AS n FROM surveys WHERE ts >= ?", (start,))["n"])

    # ------------------------------------------------------------------ темы, правила, профиль
    def topics(self, enabled_only: bool = True) -> list[dict]:
        rows = self.query("SELECT * FROM topics" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            d["include_words"] = jload(d["include_words"], [])
            d["exclude_words"] = jload(d["exclude_words"], [])
            out.append(d)
        return out

    def upsert_topic(self, name: str, description: str = "", weight: float = 1.0,
                     include_words: list | None = None, exclude_words: list | None = None,
                     enabled: bool = True) -> None:
        self.execute(
            "INSERT INTO topics(name, description, weight, include_words, exclude_words, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
            "weight=excluded.weight, include_words=excluded.include_words, exclude_words=excluded.exclude_words, "
            "enabled=excluded.enabled",
            (name, description, float(weight), jdump(include_words or []), jdump(exclude_words or []),
             int(enabled), now()))

    def delete_topic(self, name: str) -> None:
        self.execute("DELETE FROM topics WHERE name=?", (name,))

    def add_rule(self, kind: str, target: str, until: float | None = None) -> None:
        self.execute("INSERT INTO rules(kind, target, until, created_at) VALUES (?, ?, ?, ?)",
                     (kind, target, until, now()))

    def remove_rule(self, rule_id: int) -> None:
        self.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    def active_rules(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM rules WHERE until IS NULL OR until > ? ORDER BY id",
                                            (now(),))]

    def set_prior(self, feature: str, pos: float, neg: float = 0.0, source: str = "") -> None:
        self.execute("INSERT OR REPLACE INTO priors(feature, pos, neg, source, created_at) VALUES (?, ?, ?, ?, ?)",
                     (feature, float(pos), float(neg), source, now()))

    def priors(self) -> dict[str, tuple[float, float]]:
        return {r["feature"]: (float(r["pos"]), float(r["neg"])) for r in self.query("SELECT * FROM priors")}

    def profile_text(self) -> str:
        row = self.one("SELECT text FROM profile WHERE status='active' ORDER BY id DESC LIMIT 1")
        return row["text"] if row else ""

    def set_profile(self, text: str, author: str = "user", note: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE profile SET status='archived' WHERE status='active'")
            self.db.execute("INSERT INTO profile(ts, text, author, status, note) VALUES (?, ?, ?, 'active', ?)",
                            (now(), text, author, note))
            self.db.commit()

    def propose_profile(self, text: str, note: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE profile SET status='rejected' WHERE status='proposed'")
            self.db.execute("INSERT INTO profile(ts, text, author, status, note) VALUES (?, ?, 'claude', "
                            "'proposed', ?)", (now(), text, note))
            self.db.commit()

    def proposed_profile(self) -> dict | None:
        row = self.one("SELECT * FROM profile WHERE status='proposed' ORDER BY id DESC LIMIT 1")
        return dict(row) if row else None

    def resolve_proposal(self, accept: bool) -> None:
        prop = self.proposed_profile()
        if not prop:
            return
        if accept:
            self.set_profile(prop["text"], author="claude", note=prop.get("note", ""))
        self.execute("UPDATE profile SET status=? WHERE id=?", ("accepted" if accept else "rejected", prop["id"]))

    # ------------------------------------------------------------------ запуски
    def start_run(self, kind: str, query: str = "") -> int:
        return int(self.execute("INSERT INTO runs(kind, started, query) VALUES (?, ?, ?)",
                                (kind, now(), query)).lastrowid)

    def finish_run(self, run_id: int, status: str, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("found", "saved", "cost_usd", "error", "notes")}
        sets = ", ".join(["finished=?", "status=?"] + [f"{k}=?" for k in allowed])
        self.execute(f"UPDATE runs SET {sets} WHERE id=?", (now(), status, *allowed.values(), run_id))

    def last_run(self, kind: str = "collect", ok_only: bool = True) -> dict | None:
        sql = "SELECT * FROM runs WHERE kind=?" + (" AND status IN ('ok', 'partial')" if ok_only else "")
        row = self.one(sql + " ORDER BY id DESC LIMIT 1", (kind,))
        return dict(row) if row else None

    def runs(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def mark_seen_urls(self, run_id: int, urls: Iterable[str]) -> None:
        ts = now()
        with self._lock:
            self.db.executemany("INSERT OR IGNORE INTO seen_urls(url_key, run_id, ts) VALUES (?, ?, ?)",
                                [(normalize_url(u), run_id, ts) for u in urls if u])
            self.db.commit()

    def was_seen_in_run(self, run_id: int, url: str) -> bool:
        return self.one("SELECT 1 FROM seen_urls WHERE run_id=? AND url_key=?", (run_id, normalize_url(url))) \
            is not None

    # ------------------------------------------------------------------ запросы, словарь, веса
    def saved_queries(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM queries WHERE saved=1 ORDER BY id")]

    def remember_query(self, text: str, saved: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        self.execute("INSERT INTO queries(text, created_at, saved) VALUES (?, ?, ?) "
                     "ON CONFLICT(text) DO UPDATE SET saved=MAX(saved, excluded.saved)", (text, now(), int(saved)))

    def unsave_query(self, text: str) -> None:
        self.execute("UPDATE queries SET saved=0 WHERE text=?", (text,))

    def glossary(self) -> dict[str, str]:
        return {r["term_orig"]: r["term_ru"] for r in self.query("SELECT * FROM glossary ORDER BY term_orig")}

    def set_glossary(self, term_orig: str, term_ru: str) -> None:
        self.execute("INSERT OR REPLACE INTO glossary(term_orig, term_ru, created_at) VALUES (?, ?, ?)",
                     (term_orig.strip(), term_ru.strip(), now()))

    def active_weights(self) -> dict | None:
        row = self.one("SELECT data FROM weights WHERE active=1 ORDER BY id DESC LIMIT 1")
        return jload(row["data"], {}) if row else None

    def save_weights(self, data: dict, note: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE weights SET active=0")
            self.db.execute("INSERT INTO weights(ts, data, note, active) VALUES (?, ?, ?, 1)",
                            (now(), jdump(data), note))
            self.db.commit()

    # ------------------------------------------------------------------ поиск (индекс)
    def index_article(self, article_id: int, stems: str) -> None:
        if not self.fts_ok:
            return
        with self._lock:
            self.db.execute("DELETE FROM fts WHERE rowid=?", (article_id,))
            self.db.execute("INSERT INTO fts(rowid, stems) VALUES (?, ?)", (article_id, stems))
            self.db.commit()

    # ------------------------------------------------------------------ обслуживание
    def purge(self, keep_days: int) -> int:
        """Старые несохранённые статьи превращаются в «скелеты»: текст, перевод и индекс удаляются,
        тема/герои/источник остаются для долгой памяти интересов."""
        cutoff = now() - keep_days * 86400
        rows = self.query("SELECT id FROM articles WHERE purged=0 AND saved=0 AND collected_at < ?", (cutoff,))
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        with self._lock:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                marks = ",".join("?" for _ in chunk)
                self.db.execute(f"DELETE FROM blocks WHERE article_id IN ({marks})", chunk)
                if self.fts_ok:
                    self.db.execute(f"DELETE FROM fts WHERE rowid IN ({marks})", chunk)
                self.db.execute(f"UPDATE articles SET purged=1, summary_ru='[]', translate_status='none' "
                                f"WHERE id IN ({marks})", chunk)
            self.db.execute("DELETE FROM seen_urls WHERE ts < ?", (cutoff,))
            self.db.commit()
        return len(ids)

    def backup(self, directory: Path, keep: int = 3) -> Path | None:
        """Копия базы раз в сутки (SQLite backup API — безопасно при работающей базе)."""
        if keep <= 0:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"svodka-{day_key(now())}.sqlite3"
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
        for old in sorted(directory.glob("svodka-*.sqlite3"), reverse=True)[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        return target
