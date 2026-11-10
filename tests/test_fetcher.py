import ipaddress

import httpx
import pytest

from app.web import fetcher
from app.web.fetcher import FetchError, WebFetcher, check_url

PUBLIC_IP = "93.184.215.14"
# «Подменный DNS»: тесты не ходят в настоящий интернет.
DNS = {
    "localhost": ["127.0.0.1"],
    "internal.example": ["10.0.0.5"],
    # Имя, у которого один адрес публичный, а другой внутренний, — тоже запрещено.
    "mixed.example": [PUBLIC_IP, "192.168.1.10"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def resolve(host, port):
        try:
            ipaddress.ip_address(host)
            return [host]  # IP-адрес в URL «резолвится» сам в себя, как в настоящем getaddrinfo
        except ValueError:
            return DNS.get(host, [PUBLIC_IP])

    monkeypatch.setattr(fetcher, "_resolve", resolve)


def make_fetcher(handler) -> WebFetcher:
    return WebFetcher("test-agent", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def html_response(html: str | bytes, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    body = html.encode() if isinstance(html, str) else html
    return httpx.Response(200, headers={"content-type": content_type}, content=body)


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "javascript:alert(1)",
    "http://127.0.0.1/",
    "http://localhost/",
    "http://10.1.2.3/",
    "http://192.168.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",  # метаданные облака
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://internal.example/",
    "http://mixed.example/",
    "http://user:pass@example.com/",
    "http://example.com:8080/",
    "https://example.com:22/",
])
async def test_dangerous_urls_rejected(url):
    with pytest.raises(FetchError):
        await check_url(url)


async def test_good_url_normalized():
    assert await check_url("https://example.com") == "https://example.com/"
    assert await check_url(" https://example.com/a?b=1#top ") == "https://example.com/a?b=1"


async def test_extracts_title_text_and_links():
    html = """<html><head><title>Тарифы конкурента</title><meta name="description" content="Онлайн-чат"></head>
    <body><nav><a href="/blog">Блог</a><a href="/pricing">Тарифы</a><a href="https://other.example/x">Партнёр</a></nav>
    <script>var secret = 1;</script><style>.a{color:red}</style>
    <main><h1>Тариф Старт</h1><p>990 ₽ в месяц</p></main></body></html>"""

    def handler(request):
        assert request.headers["user-agent"] == "test-agent"
        return html_response(html)

    page = await make_fetcher(handler).fetch("https://rival.example/")
    assert page.title == "Тарифы конкурента"
    assert page.description == "Онлайн-чат"
    assert "990 ₽ в месяц" in page.text
    assert "secret" not in page.text and "color" not in page.text
    # «Тарифы» — важная ссылка, идёт первой; ссылки на чужие сайты не берём.
    assert page.links[0] == ("Тарифы", "https://rival.example/pricing")
    assert all("other.example" not in url for _, url in page.links)


async def test_redirect_is_followed_and_checked():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return html_response("<html><body><p>Новая страница</p></body></html>")

    page = await make_fetcher(handler).fetch("https://site.example/old")
    assert page.url == "https://site.example/new"
    assert "Новая страница" in page.text


async def test_redirect_to_internal_address_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(FetchError, match="внутреннюю сеть"):
        await make_fetcher(handler).fetch("https://rival.example/")


async def test_non_html_rejected():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")

    with pytest.raises(FetchError, match="не веб-страница"):
        await make_fetcher(handler).fetch("https://rival.example/file.pdf")


async def test_http_error_status():
    with pytest.raises(FetchError, match="404"):
        await make_fetcher(lambda r: httpx.Response(404)).fetch("https://rival.example/missing")


async def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchError, match="не ответил"):
        await make_fetcher(handler).fetch("https://rival.example/")


async def test_windows_1251_page_decoded():
    html = '<html><head><meta charset="windows-1251"><title>Мебель</title></head><body><p>Кухни на заказ</p></body></html>'
    page = await make_fetcher(lambda r: html_response(html.encode("cp1251"), "text/html")).fetch("https://old.example/")
    assert page.title == "Мебель"
    assert "Кухни на заказ" in page.text


async def test_large_page_is_truncated(monkeypatch):
    monkeypatch.setattr(fetcher, "MAX_BYTES", 1000)
    page = await make_fetcher(lambda r: html_response("<html><body>" + "а" * 5000)).fetch("https://big.example/")
    assert 0 < len(page.text) <= 1000


async def test_cache_avoids_second_download():
    calls = []

    def handler(request):
        calls.append(request.url)
        return html_response("<html><body>текст</body></html>")

    f = make_fetcher(handler)
    await f.fetch("https://rival.example/")
    await f.fetch("https://rival.example/")
    assert len(calls) == 1
