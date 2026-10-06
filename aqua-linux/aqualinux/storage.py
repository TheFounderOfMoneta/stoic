"""История диктовок (SQLite), статистика, словарь и сниппеты (JSON)."""
from __future__ import annotations

import functools
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import wave
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import AUDIO_DIR, DATA_DIR, HISTORY_DB, read_json_safely, write_json_safely

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT '',
    app TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'dictation',
    duration REAL NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0,
    latency REAL NOT NULL DEFAULT 0,
    audio TEXT
);
CREATE INDEX IF NOT EXISTS transcripts_ts ON transcripts(ts);
-- Статистика по дням и приложениям живёт отдельно от записей: старые записи удаляются
-- (по умолчанию через неделю), а «слов надиктовано» и «дней подряд» остаются.
CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    latency REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS apps (app TEXT PRIMARY KEY, words INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
STATS_VERSION = "1"
PAGE = 30           # записей на странице истории
VACUUM_AFTER = 300  # удалили столько записей — сжимаем файл


def _db_safe(default=None):
    """Ошибка базы истории не должна ломать диктовку: повреждённую базу откладываем
    в сторону и начинаем новую, остальные ошибки — в журнал."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            for attempt in range(2):
                try:
                    return fn(self, *args, **kwargs)
                except sqlite3.DatabaseError as exc:
                    log.warning("История: %s", exc)
                    if attempt == 0 and _corrupted(exc):
                        self._recreate()
                        continue
                    return default() if callable(default) else default
                except (OSError, ValueError, TypeError) as exc:
                    log.warning("История: %s", exc)
                    return default() if callable(default) else default
            return default() if callable(default) else default
        return wrapper
    return deco


def _corrupted(exc: Exception) -> bool:
    text = str(exc).lower()
    return "malformed" in text or "not a database" in text or "no such table" in text or "corrupt" in text


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class History:
    """Запись — в отдельном потоке (медленный диск или заблокированная база не тормозят
    облачко и вставку), чтение — сразу. on_change() вызывается из потока записи."""

    def __init__(self, path: Path = HISTORY_DB, on_change: Optional[Callable[[], None]] = None):
        self.path = path
        self.on_change = on_change
        self.persistent = True
        self.problem = ""                 # почему история не сохраняется на диск
        self._lock = threading.RLock()
        self._queue: "queue.Queue" = queue.Queue()
        self._writer: Optional[threading.Thread] = None
        self._closed = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._open()
        except (sqlite3.DatabaseError, OSError) as exc:
            log.warning("История недоступна (%s) — пробую начать новую", exc)
            try:
                self._recreate()
            except (sqlite3.DatabaseError, OSError) as exc2:
                self._memory(exc2)

    # ------------------------------------------------------------- база
    def _open(self) -> None:
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=2.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.execute("SELECT count(*) FROM transcripts").fetchone()
        self._ensure_stats()

    def _memory(self, exc: Exception) -> None:
        """Папка данных недоступна (нет места, нет прав, вместо папки — файл): история
        живёт в памяти до выхода, диктовка работает как обычно."""
        log.error("История не сохраняется на диск: %s", exc)
        self.persistent = False
        self.problem = str(exc)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('stats', ?)", (STATS_VERSION,))

    def _recreate(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass
        if not self.persistent:
            self._memory(RuntimeError(self.problem))
            return
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                try:
                    os.replace(src, Path(f"{self.path}.broken-{stamp}{suffix}"))
                except OSError:
                    pass
        self._open()

    def _ensure_stats(self) -> None:
        """Один раз: перенести статистику из записей в таблицы daily/apps (обновление с 1.0)."""
        row = self.db.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
        if row and row[0] == STATS_VERSION:
            return
        self._rebuild_stats()

    def _rebuild_stats(self) -> None:
        with self._lock:
            self.db.execute("DELETE FROM daily")
            self.db.execute("DELETE FROM apps")
            self.db.execute(
                "INSERT INTO daily(day, count, words, seconds, latency)"
                " SELECT date(ts,'unixepoch','localtime'), COUNT(*), SUM(words), SUM(duration), SUM(latency)"
                " FROM transcripts GROUP BY 1")
            self.db.execute("INSERT INTO apps(app, words) SELECT app, SUM(words) FROM transcripts"
                            " WHERE app != '' GROUP BY app")
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('stats', ?)", (STATS_VERSION,))
            self.db.commit()

    # ------------------------------------------------------------- поток записи
    def _submit(self, fn, *args) -> None:
        if self._closed:
            return
        with self._lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(target=self._write_loop, name="history", daemon=True)
                self._writer.start()
        self._queue.put((fn, args))

    def _write_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                fn, args = item
                changed = fn(*args)
                if changed and self.on_change is not None:
                    try:
                        self.on_change()
                    except Exception:  # noqa: BLE001
                        log.debug("on_change", exc_info=True)
            except Exception:  # noqa: BLE001 — поток записи не должен умирать
                log.exception("Ошибка записи истории")
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 3.0) -> bool:
        """Дождаться, пока всё записано (выход из приложения, тесты)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True

    def close(self, timeout: float = 3.0) -> None:
        self.flush(timeout)
        self._closed = True
        self._queue.put(None)

    # ------------------------------------------------------------- запись
    def add(self, text: str, raw: str, app: str, title: str, mode: str,
            duration: float, words: int, latency: float, audio=None) -> None:
        """audio — путь к WAV или сам звук (numpy): тогда файл пишется здесь же, в фоне."""
        self._submit(self._add, time.time(), text, raw, app, title, mode, duration, words, latency, audio)

    @_db_safe(False)
    def _add(self, ts, text, raw, app, title, mode, duration, words, latency, audio) -> bool:
        if isinstance(audio, np.ndarray):
            try:
                audio = save_wav(audio) if audio.size and self.persistent else None
            except (OSError, ValueError) as exc:
                log.warning("Не удалось сохранить звук записи: %s", exc)
                audio = None
        with self._lock:
            self.db.execute(
                "INSERT INTO transcripts(ts,text,raw,app,title,mode,duration,words,latency,audio)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, text, raw, app, title, mode, duration, words, latency, audio))
            self.db.execute(
                "INSERT INTO daily(day, count, words, seconds, latency) VALUES (?, 1, ?, ?, ?)"
                " ON CONFLICT(day) DO UPDATE SET count=count+1, words=words+excluded.words,"
                " seconds=seconds+excluded.seconds, latency=latency+excluded.latency",
                (_day(ts), int(words), float(duration), float(latency)))
            if app:
                self.db.execute("INSERT INTO apps(app, words) VALUES (?, ?)"
                                " ON CONFLICT(app) DO UPDATE SET words=words+excluded.words", (app, int(words)))
            self.db.commit()
        return True

    def update_text(self, row_id: int, text: str, words: int) -> None:
        self._submit(self._update_text, row_id, text, words)

    @_db_safe(False)
    def _update_text(self, row_id: int, text: str, words: int) -> bool:
        with self._lock:
            self.db.execute("UPDATE transcripts SET text=?, words=? WHERE id=?", (text, words, row_id))
            self.db.commit()
        return True

    def delete(self, row_id: int) -> None:
        self._submit(self._delete, row_id)

    @_db_safe(False)
    def _delete(self, row_id: int) -> bool:
        with self._lock:
            row = self.db.execute("SELECT audio FROM transcripts WHERE id=?", (row_id,)).fetchone()
            self.db.execute("DELETE FROM transcripts WHERE id=?", (row_id,))
            self.db.commit()
        if row and row["audio"]:
            _remove(row["audio"])
        return True

    def clear(self) -> None:
        self._submit(self._clear)

    @_db_safe(False)
    def _clear(self) -> bool:
        with self._lock:
            audio = [r["audio"] for r in self.db.execute("SELECT audio FROM transcripts WHERE audio IS NOT NULL")]
            self.db.execute("DELETE FROM transcripts")
            self.db.commit()
        for path in audio:
            _remove(path)
        return True

    def purge(self, keep_days: int, keep_audio_days: int) -> None:
        """Удалить записи старше keep_days (0 — хранить всегда) и звук старше keep_audio_days."""
        self._submit(self._purge, int(keep_days or 0), int(keep_audio_days or 0))

    def purge_audio(self, keep_days: int) -> None:
        self._submit(self._purge, 0, int(keep_days or 0))

    @_db_safe(False)
    def _purge(self, keep_days: int, keep_audio_days: int) -> bool:
        now = time.time()
        removed_rows, files = 0, []
        with self._lock:
            newest = self.db.execute("SELECT MAX(ts) FROM transcripts").fetchone()[0]
            if newest is not None and newest > now + 86400:
                # Часы ушли назад (сбой RTC, ручная смена даты) — не трогаем ничего:
                # по неверным часам можно удалить лишнее.
                log.warning("Часы компьютера отстают от времени записей — автоочистку пропускаю")
                return False
            if keep_days > 0:
                cutoff = now - keep_days * 86400
                files += [r["audio"] for r in self.db.execute(
                    "SELECT audio FROM transcripts WHERE ts<? AND audio IS NOT NULL", (cutoff,))]
                removed_rows = self.db.execute("DELETE FROM transcripts WHERE ts<?", (cutoff,)).rowcount
            if keep_audio_days > 0:
                cutoff = now - keep_audio_days * 86400
                rows = self.db.execute(
                    "SELECT id, audio FROM transcripts WHERE audio IS NOT NULL AND ts<?", (cutoff,)).fetchall()
                files += [r["audio"] for r in rows]
                self.db.executemany("UPDATE transcripts SET audio=NULL WHERE id=?", [(r["id"],) for r in rows])
            self.db.commit()
            referenced = {r[0] for r in self.db.execute("SELECT audio FROM transcripts WHERE audio IS NOT NULL")}
        for path in files:
            _remove(path)
        if self.persistent:
            _remove_orphan_audio(referenced)
        if removed_rows:
            log.info("Автоочистка истории: удалено записей — %d", removed_rows)
        if removed_rows >= VACUUM_AFTER and self.persistent:
            try:
                with self._lock:
                    self.db.execute("VACUUM")
            except sqlite3.DatabaseError:
                pass
        return bool(removed_rows or files)

    # ------------------------------------------------------------- чтение
    @_db_safe(list)
    def recent(self, limit: int = PAGE, query: str = "", before_id: Optional[int] = None) -> list[sqlite3.Row]:
        """Последние записи (новые сверху). before_id — следующая страница."""
        where, args = [], []
        if query:
            like = f"%{query}%"
            where.append("(text LIKE ? OR app LIKE ? OR title LIKE ?)")
            args += [like, like, like]
        if before_id is not None:
            where.append("id < ?")
            args.append(int(before_id))
        sql = "SELECT * FROM transcripts" + (" WHERE " + " AND ".join(where) if where else "")
        # По порядку добавления, а не по времени: часы компьютера могут скакать.
        with self._lock:
            return self.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()

    @_db_safe(0)
    def count(self) -> int:
        with self._lock:
            return int(self.db.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0])

    @_db_safe(None)
    def last(self) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM transcripts WHERE mode != 'command-input' ORDER BY id DESC LIMIT 1").fetchone()

    @_db_safe(None)
    def get(self, row_id: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.db.execute("SELECT * FROM transcripts WHERE id=?", (row_id,)).fetchone()

    @_db_safe(lambda: {"count": 0, "words": 0, "seconds": 0.0, "wpm": 0.0, "latency": 0.0, "week_words": 0,
                       "streak": 0, "saved_minutes": 0.0, "top_apps": [], "daily": []})
    def stats(self, typing_wpm: int = 40) -> dict:
        today = date.today()
        with self._lock:
            row = self.db.execute(
                "SELECT COALESCE(SUM(count),0) n, COALESCE(SUM(words),0) w, COALESCE(SUM(seconds),0) d,"
                " COALESCE(SUM(latency),0) l FROM daily").fetchone()
            week = self.db.execute("SELECT COALESCE(SUM(words),0) FROM daily WHERE day>=?",
                                   ((today - timedelta(days=6)).isoformat(),)).fetchone()[0]
            days = [r[0] for r in self.db.execute(
                "SELECT day FROM daily WHERE count>0 ORDER BY day DESC LIMIT 400")]
            top_apps = self.db.execute("SELECT app, words FROM apps ORDER BY words DESC LIMIT 5").fetchall()
            daily = self.db.execute("SELECT day, words FROM daily WHERE day>=? ORDER BY day",
                                    ((today - timedelta(days=13)).isoformat(),)).fetchall()
        count, words, seconds = int(row["n"]), int(row["w"]), float(row["d"])
        wpm = words / (seconds / 60) if seconds > 5 else 0.0
        saved_min = max(0.0, words / max(1, typing_wpm) - seconds / 60)
        return {
            "count": count, "words": words, "seconds": seconds, "wpm": wpm,
            "latency": float(row["l"]) / count if count else 0.0, "week_words": int(week),
            "streak": _streak(days), "saved_minutes": saved_min,
            "top_apps": [(r["app"], int(r["words"])) for r in top_apps],
            "daily": [(r["day"], int(r["words"])) for r in daily],
        }


def _remove(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_orphan_audio(referenced: set) -> None:
    """Файлы записей, на которые уже ничего не ссылается (сбой во время сохранения, ручное
    удаление базы), — удаляем, чтобы папка не росла. Свежие (< 1 ч) не трогаем."""
    try:
        cutoff = time.time() - 3600
        for entry in os.scandir(AUDIO_DIR):
            if entry.name.endswith(".wav") and entry.path not in referenced:
                try:
                    if entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                except OSError:
                    pass
    except OSError:
        pass


def _streak(days: list[str]) -> int:
    if not days:
        return 0
    today = datetime.now().date()
    dates = set()
    for d in days:
        try:
            dates.add(datetime.strptime(d, "%Y-%m-%d").date())
        except (TypeError, ValueError):
            pass
    start = today if today in dates else today - timedelta(days=1)
    streak = 0
    while start in dates:
        streak += 1
        start -= timedelta(days=1)
    return streak


def save_wav(audio: np.ndarray, sr: int = 16000) -> str:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / datetime.now().strftime("%Y%m%d_%H%M%S_%f.wav")
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return str(path)


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        data = wf.readframes(wf.getnframes())
    return np.frombuffer(data, np.int16).astype(np.float32) / 32768.0


class JsonStore:
    """Словарь, замены и сниппеты — читаемый JSON, удобно править руками."""

    def __init__(self, name: str, default):
        self.path = DATA_DIR / name
        self.default = default
        self.data = self.load()

    def load(self):
        data = read_json_safely(self.path)
        if not isinstance(data, dict):
            return json.loads(json.dumps(self.default))
        return data

    def save(self) -> None:
        try:
            write_json_safely(self.path, self.data)
        except OSError:
            pass


DICTIONARY_VERSION = 2


def _t(term: str, *sounds: str, fuzzy: bool = False) -> dict:
    return {"term": term, "sounds_like": list(sounds), "fuzzy": fuzzy}


# Как GigaAM обычно слышит английские термины в русской речи. Слова, которые часто бывают
# обычными русскими («куда», «кодекс», «питон»), сюда не входят — их можно добавить вручную.
DEFAULT_TERMS = [
    _t("GigaAM", "гигаам", "гига ам", "гига эй эм"),
    _t("Ubuntu", "убунту", "убунта", fuzzy=True),
    _t("GitHub", "гитхаб", "гит хаб", "гитхабе", fuzzy=True),
    _t("GitLab", "гитлаб", "гит лаб"),
    _t("Git", "гит"),
    _t("Python", "пайтон", "пайтоне", fuzzy=True),
    _t("Qwen", "квен", "квэн", "квин", "кьювен", "КВН"),
    _t("ChatGPT", "чат гпт", "чатгпт", "чат джипити", "чат джи пи ти", "чат GPT"),
    _t("GPT", "гпт", "джипити", "джи пи ти"),
    _t("OpenAI", "опен эй ай", "опенэйай", "оупен эй ай", "опен ай"),
    _t("Claude Code", "клод код", "клауд код"),
    _t("Claude", "клод", "клауд"),
    _t("Anthropic", "антропик", "антропика"),
    _t("DeepSeek", "дипсик", "дип сик", "дипсика"),
    _t("Gemini", "джемини", "гемини"),
    _t("Ollama", "оллама", "олама", "олламе", "олламу"),
    _t("llama.cpp", "лама цпп", "лама си пи пи", "ллама цпп"),
    _t("Hugging Face", "хаггинг фейс", "хагинг фейс", "хаггинг фейс"),
    _t("Linux", "линукс", "линуксе", "линуксом"),
    _t("Windows", "виндоус", "виндовс", "виндоуз"),
    _t("macOS", "мак ос", "макос", "мак оус"),
    _t("Docker", "докер", "докере", "докером"),
    _t("Kubernetes", "кубернетис", "кубернетес", fuzzy=True),
    _t("JavaScript", "джаваскрипт", "джава скрипт", fuzzy=True),
    _t("TypeScript", "тайпскрипт", "тайп скрипт", fuzzy=True),
    _t("NVIDIA", "нвидиа", "нвидия", "энвидиа", "энвидия"),
    _t("PyTorch", "пайторч", "пай торч", fuzzy=True),
    _t("VS Code", "вс код", "ви эс код", "вскод", "вэ эс код"),
    _t("API", "апи", "эй пи ай"),
    _t("JSON", "джейсон", "джисон"),
    _t("Wi-Fi", "вайфай", "вай фай"),
    _t("USB", "юэсби", "ю эс би"),
    _t("Aqua Voice", "аква войс", "аква воис"),
]
DEFAULT_DICTIONARY = {"version": DICTIONARY_VERSION, "terms": DEFAULT_TERMS}


def migrate_dictionary(store: "JsonStore") -> bool:
    """Добавить в словарь пользователя новые встроенные слова (один раз на версию).
    Слова, которые человек удалил раньше, после этого не возвращаются."""
    data = store.data
    try:
        version = int(data.get("version", 1) or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= DICTIONARY_VERSION:
        return False
    terms = data.setdefault("terms", [])
    known = {(t.get("term") or "").strip().lower() for t in terms}
    for entry in DEFAULT_TERMS:
        if entry["term"].lower() not in known:
            terms.append(json.loads(json.dumps(entry)))
        else:
            # У старых встроенных слов дополняем варианты произношения.
            for t in terms:
                if (t.get("term") or "").strip().lower() == entry["term"].lower():
                    have = {s.lower() for s in t.get("sounds_like", [])}
                    t.setdefault("sounds_like", []).extend(s for s in entry["sounds_like"] if s.lower() not in have)
    data["version"] = DICTIONARY_VERSION
    try:
        store.save()
    except OSError:
        pass
    return True


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def clean_terms(entries) -> list[dict]:
    """Слова словаря в правильном виде (файл правят руками — там может оказаться что угодно)."""
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not _text(e.get("term")):
            continue
        sounds = e.get("sounds_like")
        if isinstance(sounds, str):
            sounds = [sounds]
        sounds = [_text(x) for x in sounds if _text(x)] if isinstance(sounds, list) else []
        fuzzy = e.get("fuzzy", True)
        out.append({"term": _text(e.get("term")), "sounds_like": sounds,
                    "fuzzy": fuzzy if isinstance(fuzzy, bool) else True})
    return out


def clean_replacements(items) -> list[dict]:
    out = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict) or not _text(r.get("from")):
            continue
        to = r.get("to", "")
        out.append({"from": _text(r.get("from")), "to": to if isinstance(to, str) else str(to),
                    "preserve_case": r.get("preserve_case", True) is not False,
                    "strip_punct": r.get("strip_punct", True) is not False})
    return out


def sanitize_stores(dictionary: "JsonStore", replacements: "JsonStore") -> None:
    """Привести словарь и замены к правильному виду (и сохранить, если что-то исправлено)."""
    for store, key, clean in ((dictionary, "terms", clean_terms), (replacements, "replacements", clean_replacements)):
        raw = store.data.get(key)
        fixed = clean(raw)
        if fixed != raw:
            log.warning("Исправлен файл %s (неверные записи убраны)", store.path.name)
            store.data[key] = fixed
            store.save()


DEFAULT_REPLACEMENTS = {
    "replacements": [
        {"from": "мой email", "to": "name@example.com", "preserve_case": True, "strip_punct": True},
    ],
}
