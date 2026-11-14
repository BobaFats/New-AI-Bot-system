"""
Безопасное открытие веб-страниц.

Бот открывает сайты, на которые указывает клиент (например, сайт конкурента),
и официальные сайты своей компании. Адрес может прислать кто угодно, поэтому
перед каждым запросом он строго проверяется:

  * только http:// и https://, только стандартные порты 80 и 443;
  * запрещены адреса внутренней сети: localhost, 10.x, 192.168.x, 169.254.x
    (адрес метаданных облака) и т.п. Иначе через бота можно было бы обратиться
    к сервисам внутри нашего сервера. Такая атака называется SSRF;
  * каждый редирект проверяется заново;
  * ограничены размер ответа (2 МБ) и время ожидания (10 секунд);
  * принимаются только HTML и обычный текст.

Из HTML извлекаются заголовок, описание, основной текст и ссылки на другие
страницы того же сайта (например, «Тарифы»). По ним модель может перейти дальше.

Ограничение MVP: сайты, которые рисуют содержимое JavaScript'ом в браузере,
отдают почти пустой HTML. Для них понадобится «настоящий» браузер (Playwright).
"""

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

ALLOWED_PORTS = {None, 80, 443}
MAX_REDIRECTS = 5
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

# Ссылки с этими словами в тексте или адресе показываются модели первыми:
# обычно именно там цены, возможности и контакты.
KEY_LINK_WORDS = (
    "тариф", "цен", "стоим", "price", "pricing", "plan", "сравн", "compar",
    "возможност", "функц", "feature", "интеграц", "integration",
    "о компании", "about", "контакт", "contact",
)


class FetchError(Exception):
    """Страницу открыть нельзя. Текст ошибки понятен и модели, и человеку в журнале."""


@dataclass
class Page:
    url: str  # итоговый адрес после всех редиректов
    title: str
    description: str
    text: str
    # (текст ссылки, адрес) — все ссылки на этом же сайте (до 300), важные первыми.
    # Модели показывается только часть, см. select_links().
    links: list[tuple[str, str]]
    fetched_at: float  # время загрузки (unix time)


async def _resolve(host: str, port: int) -> list[str]:
    """Возвращает IP-адреса сайта. Вынесено в отдельную функцию, чтобы тесты могли её подменить."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_url(url: str) -> str:
    """Проверяет адрес. Возвращает его без «#якоря» или выбрасывает FetchError."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise FetchError("можно открывать только адреса, начинающиеся с http:// или https://")
    if not parts.hostname:
        raise FetchError("в адресе нет имени сайта")
    if parts.username or parts.password:
        raise FetchError("адреса с логином и паролем запрещены")
    try:
        port = parts.port
    except ValueError:
        raise FetchError("некорректный порт в адресе") from None
    if port not in ALLOWED_PORTS:
        raise FetchError("разрешены только стандартные порты 80 и 443")

    default_port = 443 if parts.scheme == "https" else 80
    try:
        addresses = await _resolve(parts.hostname, port or default_port)
    except OSError:
        raise FetchError(f"сайт {parts.hostname} не найден") from None

    # Все IP-адреса сайта должны быть «публичными» (is_global).
    # Остаётся риск DNS-rebinding: имя может указывать на другой IP в момент самого запроса.
    # Для MVP он приемлем: внутренние сервисы нашего сервера закрыты паролями.
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%")[0])  # "%eth0" — суффикс зоны у IPv6, отбрасываем
        if ip.version == 6 and ip.ipv4_mapped:  # IPv4, «завёрнутый» в IPv6: ::ffff:127.0.0.1
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError("адрес ведёт во внутреннюю сеть, открывать его запрещено")

    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


class WebFetcher:
    """Загружает страницы с проверками и кэшем (повторно та же страница не скачивается 30 минут)."""

    def __init__(self, user_agent: str, http: httpx.AsyncClient | None = None, cache_ttl: int = 1800):
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Language": "ru,en;q=0.8",
        }
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Page]] = {}

    async def fetch(self, url: str) -> Page:
        url = await check_url(url)
        cached = self._cache.get(url)
        if cached and cached[0] > time.time():
            return cached[1]

        try:
            page = await self._download(url)
        except httpx.TimeoutException:
            raise FetchError(f"сайт не ответил за {TIMEOUT_SECONDS} секунд") from None
        except httpx.HTTPError as e:
            raise FetchError(f"ошибка соединения с сайтом ({type(e).__name__})") from None

        self._cache[url] = (time.time() + self.cache_ttl, page)
        if len(self._cache) > 500:  # не даём кэшу расти бесконечно: удаляем самые старые записи
            for key in sorted(self._cache, key=lambda k: self._cache[k][0])[:100]:
                del self._cache[key]
        return page

    async def _download(self, url: str) -> Page:
        current = url
        # Редиректы обрабатываем сами (а не автоматически), чтобы проверить каждый новый адрес.
        for _ in range(MAX_REDIRECTS + 1):
            async with self.http.stream("GET", current, headers=self.headers, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("сайт вернул пустое перенаправление")
                    current = await check_url(urljoin(current, location))
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"сайт ответил ошибкой {resp.status_code}")
                content_type = resp.headers.get("content-type", "").lower()
                if not any(t in content_type for t in TEXT_TYPES):
                    raise FetchError(f"это не веб-страница (тип: {content_type or 'неизвестен'})")
                body = await _read_limited(resp, MAX_BYTES)
                return parse_page(
                    body, str(resp.url), charset=resp.charset_encoding,
                    plain=content_type.startswith("text/plain"),
                )
        raise FetchError("слишком много перенаправлений")

    async def aclose(self) -> None:
        await self.http.aclose()


async def _read_limited(resp: httpx.Response, limit: int) -> bytes:
    """Читает ответ по частям и останавливается на limit байтах (обрезанный HTML разбирается нормально)."""
    chunks, size = [], 0
    async for chunk in resp.aiter_bytes():
        if size + len(chunk) > limit:
            chunks.append(chunk[: limit - size])
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def parse_page(body: bytes, url: str, charset: str | None = None, plain: bool = False, max_links: int = 300) -> Page:
    """Превращает HTML в заголовок, описание, чистый текст и список ссылок."""
    if plain:
        text = body.decode(charset or "utf-8", errors="replace")
        return Page(url=url, title="", description="", text=_clean_text(text), links=[], fetched_at=time.time())

    # BeautifulSoup сам определит кодировку по <meta charset>, если сервер её не указал
    # (многие русские сайты до сих пор в windows-1251).
    soup = BeautifulSoup(body, "html.parser", from_encoding=charset)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = (meta.get("content") or "").strip() if meta else ""

    # Ссылки собираем ДО удаления меню: «Тарифы» обычно как раз в меню.
    links = _collect_links(soup, url, max_links)

    # Убираем то, что не является текстом страницы: код, стили, меню.
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template", "nav"]):
        tag.decompose()
    full_text = _main_text(soup)

    # Шапка и подвал сайта — это обычно повтор меню и контактов. Без них нужное (например, цены)
    # оказывается в начале текста и не теряется при обрезке. Если без них текста почти нет —
    # значит, на этом сайте в них и было содержимое, оставляем полную версию.
    for tag in soup(["header", "footer"]):
        tag.decompose()
    text = _main_text(soup)
    if len(text) < 200:
        text = full_text
    return Page(url=url, title=title, description=description, text=text, links=links, fetched_at=time.time())


def _main_text(soup: BeautifulSoup) -> str:
    """Основной текст: <main> или <article>, если в них есть содержимое, иначе всё <body>."""
    root = soup.find("main") or soup.find("article")
    if root is None or len(root.get_text(strip=True)) < 200:
        root = soup.body or soup
    return _clean_text(root.get_text("\n"))


def select_links(links: list[tuple[str, str]], find: str = "", limit: int = 15) -> list[tuple[str, str]]:
    """
    Выбирает, какие ссылки страницы показать модели.

    Без find — первые limit ссылок (важные: тарифы, возможности... — уже впереди).
    С find — сначала ссылки, в тексте или адресе которых есть слова из find.
    Слова сравниваются по первым 5 буквам, чтобы находились разные формы:
    «интеграция» найдёт «Интеграции с CRM», «подключить» найдёт «Подключение…».
    """
    words = [w for w in re.findall(r"\w+", find.lower()) if len(w) >= 3]
    if not words:
        return links[:limit]
    stems = [w[:5] for w in words]

    def score(link: tuple[str, str]) -> int:
        haystack = f"{link[0]} {urlsplit(link[1]).path}".lower()
        return sum(1 for s in stems if s in haystack)

    matched = sorted((l for l in links if score(l) > 0), key=score, reverse=True)  # sorted сохраняет порядок равных
    rest = [l for l in links if score(l) == 0]
    return (matched + rest)[:limit]


def _clean_text(text: str) -> str:
    """Схлопывает пробелы, убирает пустые строки и повторы подряд."""
    result: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and (not result or result[-1] != line):
            result.append(line)
    return "\n".join(result)


def _site_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _collect_links(soup: BeautifulSoup, base_url: str, limit: int) -> list[tuple[str, str]]:
    """Ссылки на другие страницы того же сайта. Важные (тарифы, возможности...) — первыми."""
    base_host = _site_host(base_url)
    important: list[tuple[str, str]] = []
    other: list[tuple[str, str]] = []
    seen = {base_url.rstrip("/")}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if urlsplit(href).scheme not in ("http", "https") or _site_host(href) != base_host:
            continue
        if href.rstrip("/") in seen:
            continue
        seen.add(href.rstrip("/"))
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:80] or urlsplit(href).path
        haystack = f"{label} {urlsplit(href).path}".lower()
        (important if any(w in haystack for w in KEY_LINK_WORDS) else other).append((label, href))
    return (important + other)[:limit]
