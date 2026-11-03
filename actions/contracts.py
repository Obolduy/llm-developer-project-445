"""Контракты действий: нормализация входа и машиночитаемые ошибки."""
from __future__ import annotations

BUSINESS_RULE_CODES = frozenset(
    {"same_tariff", "tariff_not_available", "service_already_on", "service_already_off", "same_contacts"}
)


class ActionError(Exception):
    def __init__(self, code: str, message: str = "", **hints) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.hints = hints

    def to_tool_result(self) -> dict:
        return {"error": self.code, "detail": self.message, **self.hints}


def normalize_tariff(raw: str, catalog: list[dict]) -> dict:
    """«семейный», «Семейный», «"Семейный"» → запись каталога."""
    wanted = (raw or "").strip().strip("«»\"' ").lower()
    for tariff in catalog:
        if tariff["name"].lower() == wanted:
            return tariff
    raise ActionError("unknown_tariff", f"тарифа «{raw}» нет", available=[t["name"] for t in catalog])


def normalize_service(raw: str, catalog: list[str]) -> str:
    wanted = (raw or "").strip().strip("«»\"' ").lower()
    for service in catalog:
        if service.lower() == wanted:
            return service
    raise ActionError("unknown_service", f"услуги «{raw}» нет", available=catalog)


def normalize_phone(raw: str | None) -> str | None:
    """Любая запись из 11 цифр → +7 900 123-45-67."""
    if not raw:
        return None
    d = "".join(ch for ch in raw if ch.isdigit())
    if len(d) != 11:
        raise ActionError("bad_phone", "в телефоне должно быть 11 цифр", example="+7 900 123-45-67")
    return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:]}"


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        raise ActionError("bad_email", "почта должна быть вида name@domain", example="name@example.ru")
    return value
