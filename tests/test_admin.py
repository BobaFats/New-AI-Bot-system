async def test_admin_requires_token(client):
    r = await client.get("/admin/tenants")
    assert r.status_code == 403


async def test_update_settings_changes_prompt(client):
    headers = {"X-Admin-Token": "test-admin"}
    settings = (await client.get("/admin/tenants/t/settings", headers=headers)).json()
    settings["role"] = "Ты продаёшь слонов."
    r = await client.put("/admin/tenants/t/settings", json=settings, headers=headers)
    assert r.status_code == 200

    prompt = (await client.get("/admin/tenants/t/prompt", headers=headers)).json()["prompt"]
    assert "Ты продаёшь слонов." in prompt


async def test_invalid_settings_rejected(client):
    headers = {"X-Admin-Token": "test-admin"}
    r = await client.put("/admin/tenants/t/settings", json={"llm": {"temperature": "горячо"}}, headers=headers)
    assert r.status_code == 422
