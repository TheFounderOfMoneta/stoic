"""История диктовок (SQLite), статистика, словарь и сниппеты (JSON)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from .config import AUDIO_DIR, DATA_DIR, HISTORY_DB

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
"""


class History:
    def __init__(self, path: Path = HISTORY_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def add(self, text: str, raw: str, app: str, title: str, mode: str,
            duration: float, words: int, latency: float, audio: Optional[str] = None) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO transcripts(ts,text,raw,app,title,mode,duration,words,latency,audio)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), text, raw, app, title, mode, duration, words, latency, audio))
            self.db.commit()
            return int(cur.lastrowid)

    def update_text(self, row_id: int, text: str, words: int) -> None:
        with self._lock:
            self.db.execute("UPDATE transcripts SET text=?, words=? WHERE id=?", (text, words, row_id))
            self.db.commit()

    def delete(self, row_id: int) -> None:
        with self._lock:
            row = self.db.execute("SELECT audio FROM transcripts WHERE id=?", (row_id,)).fetchone()
            if row and row["audio"]:
                try:
                    os.remove(row["audio"])
                except OSError:
                    pass
            self.db.execute("DELETE FROM transcripts WHERE id=?", (row_id,))
            self.db.commit()

    def clear(self) -> None:
        with self._lock:
            for row in self.db.execute("SELECT audio FROM transcripts WHERE audio IS NOT NULL"):
                try:
                    os.remove(row["audio"])
                except OSError:
                    pass
            self.db.execute("DELETE FROM transcripts")
            self.db.commit()

    def recent(self, limit: int = 200, query: str = "") -> list[sqlite3.Row]:
        with self._lock:
            if query:
                like = f"%{query}%"
                return self.db.execute(
                    "SELECT * FROM transcripts WHERE text LIKE ? OR app LIKE ? OR title LIKE ?"
                    " ORDER BY ts DESC LIMIT ?", (like, like, like, limit)).fetchall()
            return self.db.execute("SELECT * FROM transcripts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()

    def last(self) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM transcripts WHERE mode != 'command-input' ORDER BY ts DESC LIMIT 1").fetchone()

    def get(self, row_id: int) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.db.execute("SELECT * FROM transcripts WHERE id=?", (row_id,)).fetchone()

    def stats(self, typing_wpm: int = 40) -> dict:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(words),0) w, COALESCE(SUM(duration),0) d,"
                " COALESCE(AVG(latency),0) l FROM transcripts").fetchone()
            week_ago = time.time() - 7 * 86400
            week = self.db.execute(
                "SELECT COALESCE(SUM(words),0) w FROM transcripts WHERE ts>=?", (week_ago,)).fetchone()
            days = [r[0] for r in self.db.execute(
                "SELECT DISTINCT date(ts,'unixepoch','localtime') FROM transcripts ORDER BY 1 DESC")]
            top_apps = self.db.execute(
                "SELECT app, SUM(words) w FROM transcripts WHERE app!='' GROUP BY app ORDER BY w DESC LIMIT 5"
            ).fetchall()
            daily = self.db.execute(
                "SELECT date(ts,'unixepoch','localtime') d, SUM(words) w FROM transcripts"
                " WHERE ts>=? GROUP BY d ORDER BY d", (time.time() - 14 * 86400,)).fetchall()
        words, seconds = int(row["w"]), float(row["d"])
        wpm = words / (seconds / 60) if seconds > 5 else 0.0
        saved_min = max(0.0, words / max(1, typing_wpm) - seconds / 60)
        return {
            "count": int(row["n"]), "words": words, "seconds": seconds, "wpm": wpm,
            "latency": float(row["l"]), "week_words": int(week["w"]), "streak": _streak(days),
            "saved_minutes": saved_min, "top_apps": [(r["app"], int(r["w"])) for r in top_apps],
            "daily": [(r["d"], int(r["w"])) for r in daily],
        }

    def purge_audio(self, keep_days: int) -> None:
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            rows = self.db.execute(
                "SELECT id, audio FROM transcripts WHERE audio IS NOT NULL AND ts<?", (cutoff,)).fetchall()
            for row in rows:
                try:
                    os.remove(row["audio"])
                except OSError:
                    pass
                self.db.execute("UPDATE transcripts SET audio=NULL WHERE id=?", (row["id"],))
            self.db.commit()


def _streak(days: list[str]) -> int:
    if not days:
        return 0
    today = datetime.now().date()
    dates = {datetime.strptime(d, "%Y-%m-%d").date() for d in days if d}
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
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return json.loads(json.dumps(self.default))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


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
    if int(data.get("version", 1) or 1) >= DICTIONARY_VERSION:
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


DEFAULT_REPLACEMENTS = {
    "replacements": [
        {"from": "мой email", "to": "name@example.com", "preserve_case": True, "strip_punct": True},
    ],
}
