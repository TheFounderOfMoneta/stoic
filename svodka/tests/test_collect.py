"""Сбор, MCP-сервер, извлечение, перевод, поиск, импорт Google — с поддельным claude и локальным сайтом."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

from svodka import collect, extract, search, takeout, translate
from svodka.config import LOCK_FILE, Settings
from svodka.mcp_server import Service
from svodka.storage import Storage
from tests.conftest import FAKE_CLAUDE
from tests.web import Site

os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    settings.set("claude.command", str(FAKE_CLAUDE))
    settings.set("translate.prefetch_top", 0)
    storage = Storage(tmp_path / "db.sqlite3")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    return settings, storage, tmp_path / "db.sqlite3"


def test_collect_end_to_end(env, monkeypatch):
    settings, storage, db = env
    with Site() as site:
        urls = [site.url(p) for p in ("/en.html", "/ru.html", "/paywall.html", "/captcha.html")]
        monkeypatch.setenv("FAKE_URLS", json.dumps(urls))
        progress = []
        res = collect.run(settings, storage, force=True, on_progress=progress.append, db_path=db)
    assert res["status"] == "ok", res
    assert res["saved"] == 4                       # выдуманная ссылка отклонена сервером
    assert any("Ищу:" in p for p in progress) and any("Читаю" in p for p in progress)
    en = storage.article_by_url(urls[0])
    assert en["extract_status"] == "ok" and en["lang"] == "en" and en["translate_status"] == "none"
    types = [b["type"] for b in storage.blocks(en["id"])]
    assert {"p", "h2", "li", "quote", "img"} <= set(types)
    ru = storage.article_by_url(urls[1])
    assert ru["lang"] == "ru" and ru["translate_status"] == "not_needed"
    assert all(b["text_ru"] for b in storage.blocks(ru["id"]))
    assert storage.article_by_url(urls[2])["extract_status"] == "paywall"
    assert storage.article_by_url(urls[3])["extract_status"] in ("captcha", "failed", "short")
    run = storage.last_run("collect")
    assert run["saved"] == 4 and run["notes"].startswith("сохранено")
    # поиск по библиотеке понимает русские окончания
    found = search.search(storage, "нейросетей")
    assert found and found[0]["article"]["id"] == ru["id"]
    assert "<b>" in found[0]["snippet"]


def test_second_scheduled_collect_is_skipped(env, monkeypatch):
    settings, storage, db = env
    monkeypatch.setenv("FAKE_URLS", "[]")
    collect.run(settings, storage, force=True, db_path=db)
    assert collect.run(settings, storage, db_path=db)["status"] == "skipped"


def test_parallel_collect_is_busy(env):
    settings, storage, db = env
    with collect._Lock(LOCK_FILE):
        assert collect.run(settings, storage, force=True, db_path=db)["status"] == "busy"
        assert collect.is_running()
    assert not collect.is_running()


@pytest.mark.parametrize("mode,kind", [("auth", "auth"), ("limit", "limit")])
def test_collect_errors_are_human(env, monkeypatch, mode, kind):
    settings, storage, db = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    res = collect.run(settings, storage, force=True, db_path=db)
    assert res["status"] == "failed" and res["error_kind"] == kind
    assert "Claude" in res["message"]


def test_claude_missing(env):
    settings, storage, db = env
    settings.set("claude.command", "/nonexistent/claude")
    res = collect.run(settings, storage, force=True, db_path=db)
    assert res["error_kind"] == "not_installed"


def test_mcp_rejects_invented_and_old(env):
    settings, storage, _db = env
    run_id = storage.start_run("collect")
    svc = Service(storage, settings, run_id)
    svc.filter_new(["https://real.example/a"])
    item = {"url": "https://real.example/a", "title_ru": "Настоящая", "topic": "ИИ: развитие",
            "summary_ru": ["факт"], "published": "2001-01-01"}
    out = json.loads(svc.save_articles([item, {**item, "url": "https://fake.invalid/b"}]))
    assert out["сохранено"] == 0
    reasons = " ".join(r["причина"] for r in out["отклонено"])
    assert "старше" in reasons and "не открывается" in reasons
    week_ago = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 8 * 86400))
    out = json.loads(svc.save_articles([{**item, "published": week_ago}]))
    assert out["сохранено"] == 0 and "старше" in out["отклонено"][0]["причина"]     # неделя — уже не новости
    out = json.loads(svc.save_articles([{**item, "published": ""}]))
    assert out["сохранено"] == 0 and "дату публикации" in out["отклонено"][0]["причина"]
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 5 * 3600))
    out = json.loads(svc.save_articles([{**item, "published": fresh}]))
    assert out["сохранено"] == 1
    out = json.loads(svc.save_articles([{**item, "published": fresh}]))
    assert out["отклонено"][0]["причина"] == "уже есть в ленте"


def test_page_date_beats_claimed_date(env):
    """Claude сказал «сегодня», а сайт в метаданных — «8 дней назад»: не берём."""
    settings, storage, _db = env
    run_id = storage.start_run("collect")
    svc = Service(storage, settings, run_id)
    url = "https://real.example/old"
    svc.filter_new([url])
    storage.cache_extracted(extract.Extracted(url=url, status="ok", published=time.time() - 8 * 86400,
                                              blocks=[{"type": "p", "text": "x"}], words=300))
    out = json.loads(svc.save_articles([{"url": url, "title_ru": "Т", "topic": "ИИ: развитие", "summary_ru": ["ф"],
                                         "published": time.strftime("%Y-%m-%dT%H:%M:%S")}]))
    assert out["сохранено"] == 0 and "старше" in out["отклонено"][0]["причина"]


def test_unknown_topic_becomes_exploration(env):
    settings, storage, _db = env
    run_id = storage.start_run("collect")
    svc = Service(storage, settings, run_id)
    svc.filter_new(["https://x.example/a"])
    svc.save_articles([{"url": "https://x.example/a", "title_ru": "Т", "topic": "Квантовые сенсоры",
                        "summary_ru": ["ф"], "bucket": "core", "published": time.strftime("%Y-%m-%dT%H:%M:%S")}])
    assert storage.article_by_url("https://x.example/a")["bucket"] == "explore"


# ---------------------------------------------------------------- перевод
def _article_with_blocks(storage, n=6):
    aid = storage.add_article({"url": "https://t.example/x", "title_orig": "Title", "lang": "en",
                               "extract_status": "ok"})
    blocks = [{"type": "p", "text": f"Paragraph {i} mentions 2026 and {i * 10 + 15} percent."} for i in range(n)]
    blocks.insert(2, {"type": "code", "text": "print('keep')"})
    storage.set_blocks(aid, blocks)
    return aid


def test_translation_streams_every_block(env):
    settings, storage, _db = env
    aid = _article_with_blocks(storage)
    seen = []
    res = translate.translate_article(settings, storage, aid, on_block=lambda i, t: seen.append(i))
    assert res["status"] == "done"
    blocks = storage.blocks(aid)
    assert all(b["text_ru"] for b in blocks)
    assert blocks[2]["text_ru"] == "print('keep')"             # код не переводится
    assert len(seen) == 6
    assert storage.article(aid)["translate_status"] == "done"


def test_translation_retries_missing_blocks(env, monkeypatch):
    settings, storage, _db = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "partial")
    aid = _article_with_blocks(storage, n=9)
    res = translate.translate_article(settings, storage, aid)
    done = [b for b in storage.blocks(aid) if b["type"] == "p" and b["text_ru"]]
    assert len(done) > 6                                      # второй заход добрал часть пропусков
    assert res["status"] in ("done", "partial")


def test_translation_rejects_garbage(env, monkeypatch):
    settings, storage, _db = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "garbage")
    aid = _article_with_blocks(storage)
    translate.translate_article(settings, storage, aid)
    assert not any(b["text_ru"] for b in storage.blocks(aid) if b["type"] == "p")


def test_prefetch_stops_on_limit_and_repeated_failures(env, monkeypatch):
    """Перевод заранее не должен выжигать лимит подписки: лимит — сразу стоп, иначе — после двух неудач."""
    settings, storage, _db = env
    calls = []

    def fake_translate(_s, _st, aid, **_k):
        calls.append(aid)
        return {"status": "failed", "message": "не вышло", "error_kind": kind}
    monkeypatch.setattr(translate, "translate_article", fake_translate)
    for i in range(5):
        aid = storage.add_article({"url": f"https://t.example/p{i}", "title_ru": f"Статья {i}", "lang": "en",
                                   "extract_status": "ok", "topic": "ИИ: развитие", "summary_ru": ["ф"]})
        storage.set_blocks(aid, [{"type": "p", "text": "Some text"}])
    kind = "limit"
    assert translate.prefetch(settings, storage, 5) == 0 and len(calls) == 1
    calls.clear()
    kind = "failed"
    assert translate.prefetch(settings, storage, 5) == 0 and len(calls) == 2


def test_translation_check_numbers():
    assert translate.check_block("In 2026, 37 percent", "В 2026 году 37 процентов")
    assert not translate.check_block("In 2026, 37 percent", "В этом году треть")
    assert translate.check_block("One of 3 things", "Одна из трёх вещей")


# ---------------------------------------------------------------- извлечение
def test_extract_detects_paywall_and_captcha():
    with Site() as site:
        assert extract.read_article(site.url("/en.html")).status == "ok"
        assert extract.read_article(site.url("/paywall.html")).status == "paywall"
        assert extract.read_article(site.url("/captcha.html")).status in ("captcha", "failed")
        assert extract.read_article(site.url("/missing.html")).status == "failed"


def test_clean_blocks_drops_site_boilerplate():
    """Как на реальной странице CGTN: рубрика, даты, источник, подпись к фото — до текста;
    шаблоны и подвал с копирайтом — после."""
    body = "Chinese Premier Li Qiang on Monday chaired a State Council executive meeting to discuss work on " \
           "macro policies and investment."
    blocks = [{"type": "p", "text": t} for t in (
        "Business", "2026.09.29 14:15 GMT+8", "Updated 2026.09.29 14:15 GMT+8", "CGTN",
        "A view shows the urban landscape of Beijing, China./ VCG", body, body, body, body, body,
        "(With input from Xinhua)", "Copyright ©", "{{#contents.0.videos}}{{/contents.0.videos}}",
        "Copyright © 2024 CGTN. 京ICP备20000184号", "互联网新闻信息许可证10120180008")]
    out = [b["text"] for b in extract.clean_blocks(blocks)]
    assert out == [body] * 5 + ["(With input from Xinhua)"]
    # подзаголовок-лид из длинной фразы и заголовки разделов остаются
    lead = {"type": "p", "text": "A long standfirst explaining what the article is about in more than twelve words "
                                 "for readers."}
    h2 = {"type": "h2", "text": "Why"}
    assert extract.clean_blocks([lead, h2, {"type": "p", "text": body}])[:2] == [lead, h2]



BODY_EN = ("When a city's population reaches over 20 million, can traditional administrative service models keep up "
           "with the demands of its residents? Beijing answered with a single hotline.")


def test_clean_blocks_cgtn_opinion_page():
    """Случай со скриншота: меню, «Следите за CGTN», баннер cookie, «Согласен», «рубрика + дата», автор,
    «Поделиться Скопировано», подпись к фото — до текста; призыв писать в редакцию, лицензия и
    «горячая линия» — после."""
    junk_head = ["Home China World World Politics Business Sci-Tech Health Culture Nature Travel Sports Live "
                 "Opinions Documentaries Global Stringer Creative Lab Art & Design Radio Video Newsletters RSS",
                 "Follow CGTN on:",
                 "By continuing to browse our site you agree to our use of cookies, revised Privacy Policy and "
                 "Terms of Use. You can change your cookie settings through your browser.",
                 "I agree", "Opinion 16:15, 07-Oct-2026", "Huang Jiyuan", "Share Copied",
                 "Customers shop for products at a POP MART store in London, May 21, 2025. /Xinhua"]
    note = ("Editor's note: CGTN's First Voice provides instant commentary on breaking stories. The column "
            "clarifies emerging issues and better defines the news agenda.")
    junk_tail = ["(If you want to contribute and have specific expertise, please contact us at opinions@cgtn.com. "
                 "Follow @thouse_opinions on Twitter to discover the latest commentaries in the CGTN Opinion Section.)",
                 "互联网新闻信息许可证10120180008", "Disinformation report hotline: 010-85061466", "DOWNLOAD OUR APP"]
    blocks = [{"type": "p", "text": t} for t in junk_head + [note] + [BODY_EN] * 6 + junk_tail]
    assert [b["text"] for b in extract.clean_blocks(blocks)] == [note] + [BODY_EN] * 6
    # то же меню по-русски (так его показывала Читалка после перевода)
    ru_nav = {"type": "p", "text": "Главная Китай Мир Мир Политика Бизнес Наука и технологии Здоровье Культура "
                                   "Природа Путешествия Спорт Прямой эфир Мнения"}
    assert ru_nav not in extract.clean_blocks([ru_nav] + [{"type": "p", "text": BODY_EN}] * 3)


def test_reclean_fixes_already_saved_articles(env):
    """Статьи, сохранённые по старым правилам, перечищаются при запуске; перевод текста не теряется."""
    _settings, storage, _db = env
    aid = storage.add_article({"url": "https://news.example/a", "title_ru": "Т", "lang": "en"})
    storage.set_blocks(aid, [{"type": "p", "text": "Share Copied"},
                             {"type": "p", "text": "By continuing to browse our site you agree to our use of cookies."},
                             {"type": "p", "text": BODY_EN, "text_ru": "Перевод абзаца."},
                             {"type": "p", "text": BODY_EN}])
    storage.meta_set("clean_version", "1")
    assert extract.reclean(storage) == 1
    left = storage.blocks(aid)
    assert [b["text_orig"] for b in left] == [BODY_EN, BODY_EN] and left[0]["text_ru"] == "Перевод абзаца."
    assert extract.reclean(storage) == 0                                   # один раз


def test_free_article_with_subscribe_banner_is_not_paywall():
    """«Оформите подписку» в шапке сайта — не пейвол, если страница сама говорит, что статья бесплатная."""
    paras = "".join(f"<p>{BODY_EN} Paragraph {i}.</p>" for i in range(9))
    html = ('<html><head><script type="application/ld+json">{"isAccessibleForFree": true}</script></head>'
            f"<body><div>Оформите подписку</div><article><h1>Title</h1>{paras}</article></body></html>")
    assert extract.extract_html(html, "https://free.example/a").status == "ok"
    paywalled = html.replace('"isAccessibleForFree": true', '"isAccessibleForFree": "False"')
    assert extract.extract_html(paywalled, "https://paid.example/a").status == "paywall"

# ---------------------------------------------------------------- Google Takeout
def test_takeout_import(env, tmp_path, monkeypatch):
    settings, storage, _db = env
    watch = [{"header": "YouTube", "title": "Watched How the Chinese State Council works",
              "titleUrl": "https://www.youtube.com/watch?v=1", "subtitles": [{"name": "Systems Channel"}],
              "time": "2026-09-01T10:00:00Z"},
             {"header": "YouTube", "title": "Вы посмотрели Как устроены регуляторы ИИ",
              "titleUrl": "https://www.youtube.com/watch?v=2", "time": "2026-09-02T10:00:00Z"}]
    search_html = '<a href="https://www.google.com/search?q=anthropic+constitution">anthropic constitution</a>'
    zpath = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("Takeout/YouTube and YouTube Music/history/watch-history.json", json.dumps(watch))
        z.writestr("Takeout/My Activity/Search/MyActivity.html", search_html)
    parsed = takeout.parse(zpath)
    assert ("How the Chinese State Council works", "Systems Channel") in parsed["videos"]
    assert "Как устроены регуляторы ИИ" in [t for t, _c in parsed["videos"]]
    assert "anthropic constitution" in parsed["queries"]
    monkeypatch.setenv("FAKE_STRUCTURED", json.dumps({
        "profile": "Интересуется устройством государства и регулированием ИИ.",
        "topics": [{"name": "Регулирование ИИ", "description": "законы и регуляторы", "weight": 0.8}],
        "entities": [{"name": "Anthropic", "weight": 0.9}, {"name": "Спорт", "weight": -0.7}],
        "subtopics": [{"name": "регулирование ИИ", "weight": 0.8}]}))
    res = takeout.import_takeout(settings, storage, str(zpath))
    assert res["ok"], res
    priors = storage.priors()
    assert priors["entity:anthropic"][0] > 0 and priors["entity:спорт"][1] > 0
    assert storage.proposed_profile()["text"].startswith("Интересуется")
    assert "Регулирование ИИ" in storage.meta_get("topic_suggestions")


# ---------------------------------------------------------------- поиск
def test_search_finds_abbreviations_and_heals_old_index(env):
    """«ИИ» стеммер превращал в «и» — такой запрос ничего не находил. Старый индекс перестраивается сам."""
    _settings, storage, _db = env
    aid = storage.add_article({"url": "https://s.example/1", "title_ru": "США вводят правила для ИИ-компаний",
                               "summary_ru": ["Регулирование нейросетей"], "lang": "ru"})
    storage.index_article(aid, "сша ввод правил для и компан")          # как индексировала старая версия
    storage.meta_set("search_index_version", "1")
    assert search.ensure_index(storage) == 1
    assert search.ensure_index(storage) == 0                              # второй раз — ничего не делает
    for q in ("ИИ", "сша", "нейросети", "компании"):
        found = search.search(storage, q)
        assert found and found[0]["article"]["id"] == aid, q
