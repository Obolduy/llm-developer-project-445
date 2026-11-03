"""Идемпотентность исполнения на стороне оркестратора.

Ключ — f"{action}:{pending_id}": одно «да» — одна операция. Неудачный
исход ключ не занимает, чтобы сбой не запер повторную попытку. Для смены
тарифа дедупликация есть и в биллинге — два барьера; для услуг и
контактов этот слой единственный.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core import db

SUCCESS = ("executed", "toggled", "updated")


@dataclass
class StoredResult:
    duplicate: bool
    result: dict


async def execute(key: str, sub: str, action: str, fn: Callable[[], Awaitable[dict]]) -> StoredResult:
    row = db.fetchone("select result from idempotency where key = %s", (key,))
    if row is not None and row["result"] is not None:
        return StoredResult(duplicate=True, result=row["result"])

    result = await fn()
    if result.get("outcome") in SUCCESS:
        db.execute(
            "insert into idempotency(key, sub, action, result) values (%s, %s, %s, %s)"
            " on conflict (key) do nothing",
            (key, sub, action, json.dumps(result, ensure_ascii=False)),
        )
    return StoredResult(duplicate=False, result=result)
