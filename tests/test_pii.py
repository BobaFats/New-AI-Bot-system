from app.pii import PiiVault


def test_masks_phone_email_and_restores():
    v = PiiVault()
    masked = v.mask("Мой номер +7 (999) 123-45-67, почта ivan@example.com")
    assert "999" not in masked and "example.com" not in masked
    assert "[PHONE_1]" in masked and "[EMAIL_1]" in masked
    # Телефон нормализуется для поиска в CRM.
    assert v.by_placeholder["[PHONE_1]"] == "79991234567"
    assert v.unmask("Записал [PHONE_1]") == "Записал 79991234567"


def test_phone_formats():
    for raw in ["89991234567", "8 999 123 45 67", "+79991234567", "7-999-123-45-67"]:
        v = PiiVault()
        assert v.mask(f"тел {raw}") == "тел [PHONE_1]", raw


def test_same_value_same_placeholder():
    v = PiiVault()
    v.mask("+79991234567")
    assert v.mask("ещё раз 89991234567") == "ещё раз [PHONE_1]"
    assert v.mask("другой +79990000000") == "другой [PHONE_2]"


def test_card_only_if_luhn_valid():
    v = PiiVault()
    assert v.mask("карта 4111 1111 1111 1111") == "карта [CARD_1]"
    # Случайное 16-значное число, не проходящее проверку Луна, картой не считается.
    assert "CARD" not in PiiVault().mask("заказ 1234567812345678")


def test_inn_and_passport():
    v = PiiVault()
    assert v.mask("ИНН 1234567890") == "ИНН [INN_1]"
    assert v.mask("ИНН 123456789012") == "ИНН [INN_2]"
    assert v.mask("паспорт 45 06 123456") == "паспорт [PASSPORT_1]"


def test_known_name():
    v = PiiVault()
    assert v.mask("Меня зовут Иван", known_values={"NAME": "Иван"}) == "Меня зовут [NAME_1]"


def test_prices_not_masked():
    v = PiiVault()
    text = "Кухня от 45 000 ₽, срок 20–35 дней, артикул 12345"
    assert v.mask(text) == text


def test_unmask_obj_nested():
    v = PiiVault()
    v.mask("+79991234567")
    assert v.unmask_obj({"phone": "[PHONE_1]", "list": ["[PHONE_1]", 5]}) == {
        "phone": "79991234567", "list": ["79991234567", 5],
    }
