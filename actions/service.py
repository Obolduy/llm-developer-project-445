"""Действия поверх REST-моков: prepare_* проверяют предусловия, execute_action исполняет."""
from __future__ import annotations

import json

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from actions.contracts import (
    ActionError,
    normalize_email,
    normalize_phone,
    normalize_service,
    normalize_tariff,
)
from core import config

SERVICE_CATALOG = ["Музыка", "Роуминг", "Антивирус"]


async def _rest(method: str, path: str, body: dict | None = None) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        r = await client.request(method, f"{config.STAND_URL}{path}", json=body)
    if r.status_code == 404:
        raise ActionError("unknown_sub", "абонент не найден")
    r.raise_for_status()
    return r.json()


async def _subscriber(sub: str) -> dict:
    return await _rest("GET", f"/mock/crm/subscriber?sub={sub}")


async def prepare_tariff_change(sub: str, tariff_raw: str) -> dict:
    me = await _subscriber(sub)
    catalog = (await _rest("GET", "/mock/crm/tariffs"))["tariffs"]
    target = normalize_tariff(tariff_raw, catalog)
    if me["tariff"] == target["name"]:
        raise ActionError("same_tariff", f"тариф «{target['name']}» уже подключён", current=me["tariff"])
    if not target["available"]:
        raise ActionError("tariff_not_available", f"тариф «{target['name']}» сейчас не подключается")
    price_now = next((t["price_rub"] for t in catalog if t["name"] == me["tariff"]), "?")
    return {
        "action": "tariff_change",
        "details": {"tariff": target["name"]},
        "view": {
            "что": "смена тарифа",
            "с": me["tariff"],
            "на": target["name"],
            "плата": f"{price_now} ₽/мес → {target['price_rub']} ₽/мес",
            "вступает в силу": "с 1-го числа следующего расчётного периода",
        },
    }


async def prepare_toggle_service(sub: str, service_raw: str, enabled: bool) -> dict:
    me = await _subscriber(sub)
    service = normalize_service(service_raw, SERVICE_CATALOG)
    row = next((s for s in me["services"] if s["service"] == service), None)
    if row is None:
        raise ActionError("unknown_service", f"услуга «{service}» не подключается этому абоненту",
                          available=[s["service"] for s in me["services"]])
    if row["enabled"] == enabled:
        code = "service_already_on" if enabled else "service_already_off"
        raise ActionError(code, f"услуга «{service}» уже {'подключена' if enabled else 'отключена'}")
    return {
        "action": "service_toggle",
        "details": {"service": service, "enabled": enabled},
        "view": {
            "что": "подключение услуги" if enabled else "отключение услуги",
            "услуга": service,
            "плата": f"{row['price_rub']} ₽/мес",
        },
    }


async def prepare_contacts_update(sub: str, phone_raw: str | None, email_raw: str | None) -> dict:
    me = await _subscriber(sub)
    phone, email = normalize_phone(phone_raw), normalize_email(email_raw)
    if phone is None and email is None:
        raise ActionError("nothing_to_update", "нужен новый телефон или почта")
    details, view = {}, {"что": "обновление контактов"}
    if phone and phone != me["phone"]:
        details["phone"], view["телефон"] = phone, f"{me['phone']} → {phone}"
    if email and email != me["email"]:
        details["email"], view["почта"] = email, f"{me['email'] or '—'} → {email}"
    if not details:
        raise ActionError("same_contacts", "контакты совпадают с текущими")
    return {"action": "contacts_update", "details": details, "view": view}


async def execute_action(sub: str, action: str, details_json: str, idempotency_key: str) -> dict:
    try:
        details = json.loads(details_json or "{}")
    except ValueError as e:
        raise ActionError("bad_details", "details_json — не JSON") from e
    if action == "tariff_change":
        return await _rest("POST", "/mock/billing/tariff-change",
                           {"sub": sub, "tariff": details.get("tariff", ""), "idempotency_key": idempotency_key})
    if action == "service_toggle":
        return await _rest("POST", "/mock/crm/toggle-service",
                           {"sub": sub, "service": details.get("service", ""), "enabled": bool(details.get("enabled"))})
    if action == "contacts_update":
        return await _rest("POST", "/mock/crm/contacts",
                           {"sub": sub, "phone": details.get("phone"), "email": details.get("email")})
    raise ActionError("unknown_action", f"действия «{action}» нет",
                      available=["tariff_change", "service_toggle", "contacts_update"])


async def create_ticket(sub: str, subject: str, note: str) -> dict:
    return await _rest("POST", "/mock/tickets/create", {"sub": sub, "subject": subject, "note": note})
