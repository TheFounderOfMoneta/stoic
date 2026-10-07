"""Экономный сбор: заголовки из RSS (локальный «Bing» и лента), два коротких вызова Claude без инструментов."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from svodka import collect, pipeline, sources
from svodka.config import Settings
from svodka.storage import Storage
from tests.conftest import FAKE_CLAUDE
from tests.web import Site, rss

os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    settings.set("claude.command", str(FAKE_CLAUDE))
    settings.set("translate.prefetch_top", 0)
    storage = Storage(tmp_path / "db.sqlite3")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.setenv("FAKE_DIGEST_LOG", str(tmp_path / "digest.json"))
    monkeypatch.setattr(sources, "_polite", lambda *_a, **_k: None)     # локальный сайт, ждать незачем
    monkeypatch.setattr(pipeline, "MIN_POOL", 2)
    site = Site().__enter__()
    monkeypatch.setattr(sources, "BING", site.url("/bing?q={q}&mkt={mkt}{fresh}"))
    settings.set("collect.feeds", [{"url": site.url("/feed.xml"), "area": "russia"}])
    yield settings, storage, tmp_path / "db.sqlite3", site
    site.__exit__(None, None, None)


def test_cheap_collect_end_to_end(env):
    settings, storage, db, site = env
    progress = []
    res = collect.run(settings, storage, force=True, on_progress=progress.append, db_path=db)
    assert res["status"] == "ok", res
    assert res["saved"] == 2              # капча и недельная отсеяны; платная без текста — не очень важна
    assert any("заголовк" in p for p in progress) and any("Коротко" in p for p in progress)
    en = storage.article_by_url(site.url("/en.html"))
    assert en["title_ru"].startswith("Статья:") and en["summary_ru"] and en["extract_status"] == "ok"
    assert en["published_at"] and time.time() - en["published_at"] < 6 * 3600
    assert storage.article_by_url(site.url("/ru.html"))["translate_status"] == "not_needed"
    assert storage.article_by_url(site.url("/paywall.html")) is None
    assert storage.article_by_url(site.url("/old.html")) is None
    # Claude видит только начало статьи — не весь текст
    digest = json.loads(Path(os.environ["FAKE_DIGEST_LOG"]).read_text())
    lead = next(a["начало"] for a in digest["статьи"] if "machinery" in a["заголовок"])
    assert len(lead.split()) <= pipeline.LEAD_WORDS + 40
    run = storage.last_run("collect")
    assert run["notes"].startswith("заголовков") and run["cost_usd"] > 0
    # показанные Claude заголовки второй раз не предлагаются (экономия)
    assert storage.offered_recently([sources.normalize_url(site.url("/captcha.html"))])


def test_falls_back_to_claude_search_when_no_headlines(env, monkeypatch):
    settings, storage, db, site = env
    settings.set("collect.feeds", [{"url": site.url("/missing.xml")}])
    monkeypatch.setattr(sources, "BING", site.url("/nothing?q={q}{mkt}{fresh}"))
    monkeypatch.setenv("FAKE_URLS", json.dumps([site.url("/en.html")]))
    progress = []
    res = collect.run(settings, storage, force=True, on_progress=progress.append, db_path=db)
    assert res["status"] == "ok" and res["saved"] == 1
    assert any("с помощью Claude" in p for p in progress)


def test_limit_in_cheap_collect_is_reported(env, monkeypatch):
    settings, storage, db, _site = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    res = collect.run(settings, storage, force=True, db_path=db)
    assert res["status"] == "failed" and res["error_kind"] == "limit"


def test_new_topic_gets_queries_once(env):
    settings, storage, _db, _site = env
    storage.upsert_topic("Энергетика", "Сети, атом, накопители", 1.0)
    q, missing = sources.topic_queries(storage, storage.topics())
    assert [t["name"] for t in missing] == ["Энергетика"]
    pipeline.ensure_queries(settings, storage)
    q, missing = sources.topic_queries(storage, storage.topics())
    assert not missing and q["Энергетика"]["en"] == ["Энергетика news"]


WORDS = ["Парламент", "Губернатор", "Министерство", "Суд", "Банк", "Завод", "Университет", "Театр", "Порт",
         "Аэропорт", "Больница", "Школа", "Музей", "Стадион", "Мэрия", "Прокуратура", "Полиция", "Биржа", "Ферма",
         "Шахта", "Электростанция", "Библиотека", "Рынок", "Вокзал", "Мост", "Тоннель", "Парк", "Зоопарк",
         "Обсерватория", "Лаборатория"]


def test_prepare_filters_merges_and_caps(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    settings = Settings(tmp_path / "s.json")
    now = time.time()
    storage.add_article({"url": "https://known.example/a", "title_ru": "уже есть"})
    storage.add_rule("mute_source", "muted.example")
    C = sources.Candidate
    cands = [
        C("https://known.example/a", "Known story", published=now - 3600, origin="feed:x"),
        C("https://muted.example/a", "Muted story", published=now - 3600, origin="feed:x"),
        C("https://old.example/a", "Old story", published=now - 9 * 86400, origin="feed:x"),
        C("https://a.example/1", "OpenAI releases new reasoning model for scientists", published=now - 3600,
          origin="q:ai", topic="ИИ: развитие", source="A"),
        C("https://b.example/1", "OpenAI releases a new reasoning model for scientists today", published=now - 7200,
          origin="feed:y", source="B"),
    ] + [C(f"https://c.example/{i}", f"{w} {v} в регионе {i}", published=now - 60, origin="feed:big")
         for i, (w, v) in enumerate(zip(WORDS, reversed(WORDS)))]
    pool = sources.prepare(storage, settings, cands, now=now)
    urls = [c.url for c in pool]
    assert "https://known.example/a" not in urls and "https://muted.example/a" not in urls
    assert "https://old.example/a" not in urls
    assert pool[0].url == "https://a.example/1" and pool[0].also == ["B"]         # сюжет склеен, тема — выше
    assert "https://b.example/1" not in urls
    # одна лента и один сайт не забивают список
    assert sum(1 for c in pool if c.origin == "feed:big") == min(sources.PER_ORIGIN, sources.PER_DOMAIN)


def test_parse_feed_rss_and_atom():
    items = sources.parse_feed(rss([("Заголовок &amp; ещё", "https://x.example/1", 2)]), origin="feed:t")
    assert items[0].title == "Заголовок & ещё" and items[0].published and items[0].source == "Test feed"
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><title>A1</title>'
            '<link rel="alternate" href="https://y.example/a1"/><updated>2026-10-07T10:00:00Z</updated>'
            '<summary>&lt;p&gt;Текст&lt;/p&gt;</summary></entry></feed>')
    a = sources.parse_feed(atom)
    assert a[0].url == "https://y.example/a1" and a[0].snippet == "Текст" and a[0].published
    assert sources.parse_feed("not xml at all") == []
