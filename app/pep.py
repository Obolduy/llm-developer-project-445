"""PEP: перед каждым шагом карточки спрашивает PDP. Недоступный PDP — отказ (fail-closed)."""
from __future__ import annotations

import json
from dataclasses import dataclass

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


class Pep:
    def __init__(self, session_id: str, claims: dict, guard_state: dict) -> None:
        self.session_id = session_id
        self.subject = {
            "sub": claims.get("sub", ""),
            "role": claims.get("role", ""),
            "scopes": claims.get("scope", "").split(),
        }
        self.guard_state = guard_state
        self._opened = False

    async def _post(self, path: str, body: dict) -> httpx.Response:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            return await client.post(f"{config.PDP_URL}{path}", json=body)

    async def _open(self) -> None:
        r = await self._post("/session", {"session_id": self.session_id, "subject": self.subject})
        r.raise_for_status()
        self._opened = True

    async def _ask(self, path: str, action: str, card) -> Decision:
        body = {
            "session_id": self.session_id,
            "action": action,
            "input": {
                "action": card.action,
                "pending_id": card.pending_id,
                "details": json.dumps(card.details, ensure_ascii=False),
                "guard_ok": bool(self.guard_state.get("ok", False)),
                **self.subject,
            },
        }
        try:
            if not self._opened:
                await self._open()
            r = await self._post(path, body)
            if r.status_code == 404:  # PDP перезапущен и сессию забыл
                await self._open()
                r = await self._post(path, body)
            r.raise_for_status()
            answer = r.json()
        except httpx.HTTPError:
            self._opened = False
            return Decision(False, "pdp_unavailable")
        return Decision(answer.get("decision") == "Allow", answer.get("reason", ""))

    async def prepare(self, card) -> Decision:
        return await self._ask("/authorize", "Prepare", card)

    async def confirm(self, card) -> Decision:
        return await self._ask("/confirm", "ConfirmCard", card)

    async def execute(self, card) -> Decision:
        return await self._ask("/authorize", "Execute", card)
