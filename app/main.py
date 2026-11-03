"""Backend + оркестратор: HTTP-граница чата.

    uvicorn app.main:app --host 127.0.0.1 --port 8000

  POST /api/session                      {"user": "anna"} → открыть сессию
  POST /api/chat                         {"session_id", "message"} → SSE-поток хода
  POST /api/cards/{pending_id}/decision  подтверждение карточки (с контрактов)

События SSE хода: plan / card / reply / error / done.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import cards, orchestrator, permissions, sessions
from core import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.setup_model()
    yield


app = FastAPI(title="operator-chat-ai", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "backend"}


@app.post("/api/session")
async def create_session(body: dict) -> dict:
    login = body.get("user", "")
    if login not in config.USERS:
        raise HTTPException(400, f"неизвестный пользователь: {login}")
    session = await sessions.open_session(login)
    return {
        "session_id": session.session_id,
        "name": session.user.name,
        "role": session.user.role,
        "visible_tools": permissions.visible_tools(session.allowlist, session.pool.tool_names()),
    }


@app.post("/api/cards/{pending_id}/decision")
async def decide_card(pending_id: str, body: dict) -> dict:
    """Кнопка карточки: «да» исполняет, «нет» отменяет — код, не LLM."""
    session = sessions.get(body.get("session_id", ""))
    if session is None:
        raise HTTPException(404, "сессия не найдена")
    card = cards.active(session)
    if card is None or card.pending_id != pending_id:
        return {"outcome": "no_card", "text": "Карточка не найдена или истекла — попросите заново."}
    if body.get("answer") == "yes":
        session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"
        return {"outcome": "confirmed", "text": await orchestrator.execute_pending(session, card)}
    if body.get("answer") == "no":
        orchestrator.reject(session, card)
        return {"outcome": "rejected", "text": orchestrator.CANCELLED}
    raise HTTPException(400, "answer: yes|no")


@app.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    session = sessions.get(body.get("session_id", ""))
    if session is None:
        raise HTTPException(404, "сессия не найдена — откройте новую")
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "пустое сообщение")

    queue: asyncio.Queue = asyncio.Queue()

    async def turn() -> None:
        try:
            await orchestrator.run_turn(session, message, queue.put)
        except Exception as e:  # noqa: BLE001 — сбой хода уходит событием, поток не падает
            await queue.put({"type": "error", "detail": str(e)[:200]})
        finally:
            await queue.put({"type": "done"})

    task = asyncio.create_task(turn())

    async def stream():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] == "done":
                    break
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")
