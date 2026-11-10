"""
Поиск в интернете.

Провайдер — Yandex Search API (Yandex Cloud / AI Studio): работает из РФ, оплата в рублях.
Документация: https://aistudio.yandex.ru/docs/ru/search-api/operations/web-search-sync

Нужны две настройки в .env:
    YANDEX_SEARCH_API_KEY — API-ключ сервисного аккаунта с ролью search-api.webSearch.user
    YANDEX_FOLDER_ID      — идентификатор каталога в Yandex Cloud

Интерфейс SearchProvider позволяет заменить провайдера (например, на SearXNG),
не трогая остальной код.
"""

import base64
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.logs import console


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(Exception):
    """Поиск не удался (ошибка провайдера или сети)."""


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


class YandexSearch:
    URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

    def __init__(self, api_key: str, folder_id: str, http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.folder_id = folder_id
        self.http = http or httpx.AsyncClient(timeout=15)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        body = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query[:400],  # API принимает не больше 400 символов
                "familyMode": "FAMILY_MODE_MODERATE",
            },
            # GROUP_MODE_DEEP группирует результаты по сайтам: получаем разные сайты, а не 5 страниц одного.
            # Числа API ожидает строками.
            "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": str(limit), "docsInGroup": "1"},
            "maxPassages": "2",
            "l10n": "LOCALIZATION_RU",
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
        }
        try:
            resp = await self.http.post(self.URL, json=body, headers={"Authorization": f"Api-Key {self.api_key}"})
        except httpx.HTTPError as e:
            raise SearchError(f"нет связи с поиском ({type(e).__name__})") from None
        if resp.status_code != 200:
            raise SearchError(f"поиск ответил {resp.status_code}: {resp.text[:200]}")
        # Результат приходит в поле rawData: это XML, закодированный в base64.
        raw = base64.b64decode(resp.json().get("rawData", ""))
        return parse_yandex_xml(raw, limit)


def parse_yandex_xml(raw: bytes, limit: int = 5) -> list[SearchResult]:
    """
    Разбирает XML-ответ Яндекса. Формат (сокращённо):
        <yandexsearch><response><results><grouping><group>
            <doc><url>…</url><domain>…</domain><title>…<hlword>…</hlword>…</title>
                 <headline>…</headline><passages><passage>…</passage></passages></doc>
        </group>…</grouping></results></response></yandexsearch>
    Ищем все <doc> в любом месте документа, поэтому разбор не сломается,
    если Яндекс немного изменит вложенность.
    """
    if not raw:
        return []
    root = ET.fromstring(raw)
    error = root.find(".//response/error")
    if error is not None:
        if error.get("code") == "15":  # код 15 у Яндекса: «ничего не найдено»
            return []
        raise SearchError(f"ошибка поиска {error.get('code')}: {_text(error)}")

    results: list[SearchResult] = []
    for doc in root.iter("doc"):
        url = (doc.findtext("url") or "").strip()
        if not url:
            continue
        title = _text(doc.find("title")) or (doc.findtext("domain") or url)
        passages = [p for p in (_text(x) for x in doc.iter("passage")) if p]
        snippet = " … ".join(passages) or _text(doc.find("headline"))
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _text(el: ET.Element | None) -> str:
    """Текст элемента вместе с вложенными тегами подсветки (<hlword>)."""
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def build_search_provider(settings: Settings) -> SearchProvider | None:
    """Создаёт провайдера по настройкам. None означает, что поиск выключен."""
    if settings.search_provider == "yandex":
        if not settings.yandex_search_api_key or not settings.yandex_folder_id:
            console.warning("SEARCH_PROVIDER=yandex, но не заданы YANDEX_SEARCH_API_KEY или YANDEX_FOLDER_ID: поиск выключен")
            return None
        return YandexSearch(settings.yandex_search_api_key, settings.yandex_folder_id)
    return None
