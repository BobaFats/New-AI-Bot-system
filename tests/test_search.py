import base64
import json

import httpx
import pytest

from app.web.search import SearchError, YandexSearch, parse_yandex_xml

SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0"><response date="20261110T120000">
<results><grouping attr="d" mode="deep">
<group><doc id="1"><url>https://rival.example/</url><domain>rival.example</domain>
<title><hlword>Rival</hlword> — онлайн-чат</title>
<passages><passage>Тарифы <hlword>Rival</hlword> для бизнеса</passage><passage>Попробуйте бесплатно</passage></passages></doc></group>
<group><doc id="2"><url>https://other.example/</url><domain>other.example</domain><headline>Обзор сервисов</headline></doc></group>
</grouping></results></response></yandexsearch>"""


def test_parse_results():
    results = parse_yandex_xml(SAMPLE.encode())
    assert len(results) == 2
    assert results[0].title == "Rival — онлайн-чат"  # теги подсветки <hlword> убраны, текст сохранён
    assert results[0].url == "https://rival.example/"
    assert results[0].snippet == "Тарифы Rival для бизнеса … Попробуйте бесплатно"
    assert results[1].title == "other.example"  # нет заголовка — показываем домен
    assert results[1].snippet == "Обзор сервисов"


def test_limit():
    assert len(parse_yandex_xml(SAMPLE.encode(), limit=1)) == 1


def test_nothing_found_is_empty_list():
    xml = '<yandexsearch><response><error code="15">Искомая комбинация слов нигде не встречается</error></response></yandexsearch>'
    assert parse_yandex_xml(xml.encode()) == []


def test_other_error_raises():
    xml = '<yandexsearch><response><error code="32">Лимит запросов исчерпан</error></response></yandexsearch>'
    with pytest.raises(SearchError, match="32"):
        parse_yandex_xml(xml.encode())


async def test_request_format():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        raw = base64.b64encode(SAMPLE.encode()).decode()
        return httpx.Response(200, json={"rawData": raw})

    search = YandexSearch("KEY", "FOLDER", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = await search.search("Rival тарифы", limit=3)

    assert seen["auth"] == "Api-Key KEY"
    assert seen["body"]["folderId"] == "FOLDER"
    assert seen["body"]["query"]["queryText"] == "Rival тарифы"
    assert seen["body"]["groupSpec"]["groupsOnPage"] == "3"
    assert results[0].url == "https://rival.example/"


async def test_http_error_raises():
    search = YandexSearch("KEY", "FOLDER", http=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"message": "Permission denied"}))))
    with pytest.raises(SearchError, match="403"):
        await search.search("запрос")
