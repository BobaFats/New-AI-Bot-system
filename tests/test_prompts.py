import json
from pathlib import Path

from app.agent.prompts import build_system_prompt
from app.tenant_settings import TenantSettings, load_settings


def test_competition_rules_only_when_enabled():
    s = load_settings({"company_name": "Jivo", "competition": {"enabled": True, "our_advantages": ["Все каналы в одном окне"]}})
    prompt = build_system_prompt(s)
    assert "Ты представляешь Jivo" in prompt
    assert "- Все каналы в одном окне" in prompt
    assert "Сравнение с конкурентами" not in build_system_prompt(load_settings({}))


def test_web_rules_depend_on_available_tools():
    s = load_settings({"web": {"enabled": True, "own_sites": ["https://www.jivo.ru/pricing/"]}})
    assert "Работа с сайтами" not in build_system_prompt(s, [])

    without_search = build_system_prompt(s, ["fetch_url"])
    assert "попроси клиента прислать" in without_search
    assert "https://www.jivo.ru/pricing/" in without_search

    assert "найди его инструментом web_search" in build_system_prompt(s, ["fetch_url", "web_search"])


def test_tenant_files_are_valid():
    """Все файлы tenants/*.json должны проходить проверку настроек."""
    files = list((Path(__file__).parent.parent / "tenants").glob("*.json"))
    assert files
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        TenantSettings.model_validate(data["settings"])
