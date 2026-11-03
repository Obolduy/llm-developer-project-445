"""Права сессии: allowlist из claims, два барьера оркестратора.

Роль задаёт набор операций, scope его сужает. Барьер 1 (visible_tools)
работает при сборке агентов: запрещённого модель не видит. Барьер 2
(admit) допускает «субъект × операция × объект» в момент вызова.
execute_action нет ни в одном наборе — его вызывает только код.
"""
from __future__ import annotations

from dataclasses import dataclass, field

READ_OPS = ("get_my_profile", "get_my_tariff", "list_tariffs", "list_my_charges", "search_kb")
WRITE_OPS = ("prepare_tariff_change", "prepare_toggle_service", "prepare_contacts_update")
OPERATOR_OPS = ("get_subscriber_overview",)  # чужой id легитимен, роль проверяет сервер
ESCALATION_OPS = ("create_ticket",)          # позвать человека можно всегда

# Идентичность живёт в токене. phone сюда не входит: в prepare_contacts_update
# это новое значение контакта, а не объект операции.
IDENTITY_ARGS = ("user_id", "account", "subscriber", "msisdn", "sub")


@dataclass
class Allowlist:
    sub: str
    role: str
    scopes: list[str] = field(default_factory=list)
    read_ops: list[str] = field(default_factory=list)
    write_ops: list[str] = field(default_factory=list)


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def allowlist_from_claims(claims: dict) -> Allowlist:
    role = claims.get("role", "")
    scopes = claims.get("scope", "").split()
    read_ops = [*READ_OPS, *ESCALATION_OPS]
    if role == "operator":
        read_ops += OPERATOR_OPS
    write_ops = list(WRITE_OPS) if role == "client" and "tariffs:write" in scopes else []
    return Allowlist(claims["sub"], role, scopes, read_ops, write_ops)


def visible_tools(allowlist: Allowlist, names: list[str]) -> list[str]:
    """Барьер 1: только то, что есть в allowlist сессии."""
    allowed = {*allowlist.read_ops, *allowlist.write_ops}
    return [n for n in names if n in allowed]


def admit(allowlist: Allowlist, operation: str, args: dict | None = None) -> Decision:
    """Барьер 2: операция из allowlist и без идентичности в аргументах."""
    if operation not in {*allowlist.read_ops, *allowlist.write_ops}:
        return Decision(False, f"operation_not_allowed:{operation}")
    if operation not in OPERATOR_OPS:
        bad = sorted(k for k in (args or {}) if k in IDENTITY_ARGS)
        if bad:
            return Decision(False, f"object_from_arguments:{','.join(bad)}")
    return Decision(True)
