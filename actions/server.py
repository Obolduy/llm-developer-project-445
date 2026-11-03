"""Action-MCP под JWT: субъект из проверенного токена, execute_action вызывает только код.

    uvicorn actions.server:app --host 127.0.0.1 --port 8220
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.routing import Mount

from actions import service as svc
from actions.contracts import ActionError
from core.config import RESOURCES
from core.server_auth import make_mcp_server

ACTION_ERROR_CODE = -32002

mcp = make_mcp_server(
    "actions",
    resource=RESOURCES["actions"],
    instructions="Действия абонента. prepare_* только готовят карточку, исполнение — после «да».",
)


def _sub() -> str:
    token = get_access_token()
    assert token is not None, "вызов без проверенного токена"
    return token.subject


async def _run(coro) -> dict:
    try:
        return await coro
    except ActionError as e:
        raise MCPError(code=ACTION_ERROR_CODE, message=e.code, data=e.to_tool_result()) from e


@mcp.tool()
async def prepare_tariff_change(tariff: str) -> dict:
    """Подготовить смену тарифа: карточка с разницей цен. Ничего не исполняет."""
    return {"ok": True, **await _run(svc.prepare_tariff_change(_sub(), tariff))}


@mcp.tool()
async def prepare_toggle_service(service: str, enabled: bool) -> dict:
    """Подготовить включение (enabled=true) или отключение услуги. Ничего не исполняет."""
    return {"ok": True, **await _run(svc.prepare_toggle_service(_sub(), service, enabled))}


@mcp.tool()
async def prepare_contacts_update(phone: str | None = None, email: str | None = None) -> dict:
    """Подготовить обновление телефона и/или почты. Передай только то, что меняется."""
    return {"ok": True, **await _run(svc.prepare_contacts_update(_sub(), phone, email))}


@mcp.tool()
async def execute_action(action: str, details_json: str, pending_id: str, idempotency_key: str) -> dict:
    """Исполнить подтверждённое действие. Вызывает только код оркестратора после «да»."""
    return await _run(svc.execute_action(_sub(), action, details_json, idempotency_key))


@mcp.tool()
async def create_ticket(subject: str, note: str) -> dict:
    """Создать обращение живому оператору: subject — тема, note — суть обращения."""
    return await _run(svc.create_ticket(_sub(), subject, note))


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[Mount("/actions", app=mcp.streamable_http_app())], lifespan=lifespan)
