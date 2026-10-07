"""Локальный «интернет» для тестов: статьи, пейвол, капча — на 127.0.0.1."""
from __future__ import annotations

import http.server
import threading
import time

EN_PARAS = [
    "Governments are quietly rebuilding the machinery that decides how artificial intelligence is deployed. "
    "In 2026 at least 14 agencies published new procurement rules, and the details matter more than the speeches.",
    "The most important change is procedural: every model above a compute threshold now needs a documented "
    "evaluation before it touches public services. Officials say the rule covers 37 percent of current pilots.",
    "Critics argue that the checklists reward paperwork over safety. Supporters reply that a slow, boring "
    "process is exactly what institutions need when the technology changes faster than the law.",
    "What is new is the feedback loop. Agencies must report incidents within 72 hours, and the reports feed "
    "a shared registry that other departments can query before they buy similar systems.",
    "The registry already holds 1,200 entries. Most describe ordinary failures: wrong answers in forms, "
    "missing translations, timeouts during peak hours. Only a handful involve real harm.",
    "That pattern suggests the hard part is not the model but the surrounding system — data quality, "
    "escalation paths and the people who decide when to switch the automation off.",
]
RU_PARAS = [
    "Нейросеть для обработки обращений граждан запустили в трёх регионах. За первый месяц она разобрала "
    "около 40 тысяч писем и передала людям только спорные случаи.",
    "Главное изменение — не в модели, а в регламенте: теперь у каждого решения есть ответственный "
    "сотрудник, а жалобы на автоматические ответы рассматриваются за пять рабочих дней.",
    "Эксперты отмечают, что такие системы работают только там, где заранее описаны процессы. "
    "Где процессов нет, нейросети лишь ускоряют хаос.",
    "В следующем году регламент планируют распространить ещё на 12 регионов, а результаты публиковать "
    "ежеквартально в открытом реестре.",
]


# Статьи «опубликованы» три часа назад: сервер принимает только свежее (тесты не стареют со временем).
FRESH = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))


def article_html(title: str, paras: list[str], lang: str = "en") -> str:
    body = "".join(f"<p>{p}</p>" for p in paras[:3])
    body += "<h2>How it works</h2>" if lang == "en" else "<h2>Как это работает</h2>"
    body += "<ul><li>Evaluation before deployment</li><li>Incident reports in 72 hours</li></ul>" \
        if lang == "en" else "<ul><li>Ответственный за решение</li><li>Жалобы за пять дней</li></ul>"
    body += f"<blockquote>{paras[2]}</blockquote>"
    body += '<figure><img src="/img/chart.png" alt="chart"><figcaption>Chart</figcaption></figure>'
    body += "".join(f"<p>{p}</p>" for p in paras[3:])
    return (f'<html lang="{lang}"><head><title>{title}</title>'
            f'<meta property="article:published_time" content="{FRESH}"></head>'
            f"<body><nav>Home · About · Subscribe</nav><article><h1>{title}</h1>{body}</article>"
            f"<footer>© Test site</footer></body></html>")


PAGES = {
    "/en.html": article_html("How governments rebuild the machinery of AI decisions", EN_PARAS * 2),
    "/ru.html": article_html("Нейросеть разбирает обращения граждан", RU_PARAS * 2, lang="ru"),
    "/paywall.html": "<html><body><article><h1>Big story</h1><p>Only the first lines are free.</p>"
                     "<p>Subscribe to continue reading this article.</p></article></body></html>",
    "/captcha.html": "<html><body><h1>Verify you are human</h1><p>We've detected unusual activity.</p></body></html>",
}


def rss(items: list[tuple[str, str, float]], title: str = "Test feed") -> str:
    """Лента RSS: (заголовок, ссылка, сколько часов назад)."""
    import email.utils
    out = "".join(f"<item><title>{t}</title><link>{link}</link><description>Описание: {t}</description>"
                  f"<pubDate>{email.utils.formatdate(time.time() - h * 3600)}</pubDate></item>"
                  for t, link, h in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{out}</channel></rss>'


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path.startswith("/bing"):           # «Bing News»: ссылки-переходники с адресом в url=
            from urllib.parse import quote
            items = [("How governments rebuild the machinery of AI decisions",
                      "http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=" + quote(base + "/en.html"), 3),
                     ("Old story from last week", "http://www.bing.com/news/apiclick.aspx?url="
                      + quote(base + "/old.html"), 24 * 8)]
            page = rss(items, "Bing")
        elif self.path == "/feed.xml":
            page = rss([("Нейросеть разбирает обращения граждан", base + "/ru.html", 2),
                        ("Big story behind a paywall", base + "/paywall.html", 5),
                        ("Robots check", base + "/captcha.html", 6),
                        ("How governments rebuild the machinery of AI decisions — analysis", base + "/en.html", 4)])
        else:
            page = PAGES.get(self.path)
        if page is None:
            self.send_response(404)
            self.end_headers()
            return
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class Site:
    def __enter__(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __exit__(self, *exc):
        self.server.shutdown()
