"""
Маскирование персональных данных (ПДн).

Зачем: DeepSeek — зарубежный сервис. Чтобы не передавать ему ПДн клиентов,
перед отправкой заменяем их на «заглушки»:

    "Мой телефон +7 999 123-45-67"  ->  "Мой телефон [PHONE_1]"

Реальные значения остаются у нас в «сейфе» (таблица pii_values).
Перед отправкой ответа клиенту заглушки заменяются обратно (unmask).

Что ищем регулярными выражениями: email, телефоны, номера карт, ИНН, паспорта.
Имена регуляркой надёжно не найти, поэтому маскируем только известные имена
(например, имя из профиля Jivo) — их передают в known_values.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PiiValue

# --- Регулярные выражения ---
# (?<!\w) и (?!\w) — «не часть более длинного слова/числа».
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Карта: 13–19 цифр, допускаются пробелы и дефисы между группами.
CARD_RE = re.compile(r"(?<![\w+])(?:\d[ -]?){12,18}\d(?!\w)")
# Телефон: +7 / 8 / 7 и далее 10 цифр с любыми разделителями; либо международный +XXX...
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+7|8|7)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)"
    r"|(?<!\w)\+\d[\d\s\-()]{8,16}\d(?!\w)"
)
# Паспорт РФ: 4 цифры серии + пробел + 6 цифр номера.
PASSPORT_RE = re.compile(r"(?<!\w)\d{2}\s?\d{2}\s\d{6}(?!\w)")
# ИНН: 10 цифр (юрлицо) или 12 цифр (физлицо/ИП).
INN_RE = re.compile(r"(?<!\w)(?:\d{12}|\d{10})(?!\w)")

# Шаблон заглушки, например [PHONE_1]
PLACEHOLDER_RE = re.compile(r"\[(?:PHONE|EMAIL|CARD|INN|PASSPORT|NAME)_\d+\]")


def _luhn_ok(digits: str) -> bool:
    """Проверка номера карты по алгоритму Луна — отсекает случайные длинные числа."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass
class PiiVault:
    """
    Сейф одного чата: связь «заглушка <-> реальное значение».
    new_items — то, что добавилось при последнем маскировании (нужно сохранить в БД).
    """

    by_placeholder: dict[str, str] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)
    new_items: list[tuple[str, str, str]] = field(default_factory=list)  # (placeholder, kind, value)

    def _placeholder_for(self, kind: str, value: str) -> str:
        # Одинаковое значение в одном чате всегда получает одну и ту же заглушку.
        for ph, existing in self.by_placeholder.items():
            if existing == value and self.kinds.get(ph) == kind:
                return ph
        # Номер = сколько заглушек этого типа уже есть + 1.
        n = sum(1 for k in self.kinds.values() if k == kind) + 1
        ph = f"[{kind}_{n}]"
        self.by_placeholder[ph] = value
        self.kinds[ph] = kind
        self.new_items.append((ph, kind, value))
        return ph

    def mask(self, text: str, known_values: dict[str, str] | None = None) -> str:
        """Заменяет ПДн в тексте на заглушки. known_values: {"NAME": "Иван"}."""
        if not text:
            return text

        # Порядок важен: сначала email (содержит цифры), потом карты, телефоны и т.д.
        text = EMAIL_RE.sub(lambda m: self._placeholder_for("EMAIL", m.group()), text)

        def card_sub(m: re.Match) -> str:
            digits = re.sub(r"\D", "", m.group())
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                return self._placeholder_for("CARD", digits)
            return m.group()  # не карта — оставляем как есть

        text = CARD_RE.sub(card_sub, text)
        text = PHONE_RE.sub(lambda m: self._placeholder_for("PHONE", _normalize_phone(m.group())), text)
        text = PASSPORT_RE.sub(lambda m: self._placeholder_for("PASSPORT", m.group()), text)
        text = INN_RE.sub(lambda m: self._placeholder_for("INN", m.group()), text)

        # Известные значения (например, имя клиента из Jivo).
        for kind, value in (known_values or {}).items():
            if value and len(value) >= 2:
                pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
                if pattern.search(text):
                    ph = self._placeholder_for(kind, value)
                    text = pattern.sub(ph, text)
        return text

    def unmask(self, text: str) -> str:
        """Заменяет заглушки обратно на реальные значения (для ответа клиенту и запросов в CRM)."""
        if not text:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self.by_placeholder.get(m.group(), m.group()), text)

    def unmask_obj(self, obj):
        """То же, что unmask, но для вложенных структур (аргументы инструментов)."""
        if isinstance(obj, str):
            return self.unmask(obj)
        if isinstance(obj, list):
            return [self.unmask_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v) for k, v in obj.items()}
        return obj


def _normalize_phone(raw: str) -> str:
    """Приводит телефон к виду 79991234567 (так удобнее искать в CRM)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


async def load_vault(session: AsyncSession, tenant_id: int, chat_id: str) -> PiiVault:
    """Загружает сейф чата из БД."""
    rows = await session.scalars(
        select(PiiValue).where(PiiValue.tenant_id == tenant_id, PiiValue.chat_id == chat_id)
    )
    vault = PiiVault()
    for row in rows:
        vault.by_placeholder[row.placeholder] = row.value
        vault.kinds[row.placeholder] = row.kind
    return vault


def save_vault(session: AsyncSession, tenant_id: int, chat_id: str, vault: PiiVault) -> None:
    """Добавляет в сессию новые значения сейфа (сохранятся при session.commit())."""
    for placeholder, kind, value in vault.new_items:
        session.add(PiiValue(tenant_id=tenant_id, chat_id=chat_id, placeholder=placeholder, kind=kind, value=value))
    vault.new_items.clear()
