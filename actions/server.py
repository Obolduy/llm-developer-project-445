"""Action-MCP: инструменты действий.

    uvicorn actions.server:app --host 127.0.0.1 --port 8220

prepare_* — для модели: возвращают карточку, ничего не исполняют.
execute_action — не для модели: его вызывает код оркестратора после «да».
create_ticket — эскалация оператору без карточки.
ActionError → MCPError с кодом и data: подсказки доезжают до модели.

TODO(права): перевод сервера под JWT —
  make_mcp_server("actions", resource=RESOURCES["actions"]) из
  core.server_auth (OAuth 2.1 resource server: Bearer в каждом вызове,
  проверка подписи/iss/aud); убрать sub из всех контрактов — субъект из
  get_access_token().subject (mcp.server.auth.middleware.auth_context).
  До этого шага аргумент sub — осознанная дыра; попытка протащить чужой
  sub станет тестом IDOR.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.routing import Mount

from actions import service as svc
from actions.contracts import ActionError

ACTION_ERROR_CODE = -32002

mcp = MCPServer(
    name="actions",
    instructions="Действия абонента. prepare_* только готовят карточку, исполнение — после «да».",
)


async def _run(coro) -> dict:
    try:
        return await coro
    except ActionError as e:
        raise MCPError(code=ACTION_ERROR_CODE, message=e.code, data=e.to_tool_result()) from e


@mcp.tool()
async def prepare_tariff_change(sub: str, tariff: str) -> dict:
    """Подготовить смену тарифа: карточка с разницей цен. Ничего не исполняет."""
    return {"ok": True, **await _run(svc.prepare_tariff_change(sub, tariff))}


@mcp.tool()
async def prepare_toggle_service(sub: str, service: str, enabled: bool) -> dict:
    """Подготовить включение (enabled=true) или отключение услуги. Ничего не исполняет."""
    return {"ok": True, **await _run(svc.prepare_toggle_service(sub, service, enabled))}


@mcp.tool()
async def prepare_contacts_update(sub: str, phone: str | None = None, email: str | None = None) -> dict:
    """Подготовить обновление телефона и/или почты. Передай только то, что меняется."""
    return {"ok": True, **await _run(svc.prepare_contacts_update(sub, phone, email))}


@mcp.tool()
async def execute_action(sub: str, action: str, details_json: str, pending_id: str, idempotency_key: str) -> dict:
    """Исполнить подтверждённое действие. Вызывает только код оркестратора после «да»."""
    return await _run(svc.execute_action(sub, action, details_json, idempotency_key))


@mcp.tool()
async def create_ticket(sub: str, subject: str, note: str) -> dict:
    """Создать обращение живому оператору: subject — тема, note — суть обращения."""
    return await _run(svc.create_ticket(sub, subject, note))


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[Mount("/actions", app=mcp.streamable_http_app())], lifespan=lifespan)
