"""Редактор: собирает финальный ответ абоненту из докладов агентов.

Единственный агент, чей текст уходит пользователю.
"""
from __future__ import annotations

from app import llm
from app.workers import CATALOG

EDITOR_PROMPT = """\
Ты редактор чата мобильного оператора. Тебе дают сообщение абонента и
доклады агентов. Напиши абоненту ответ: кратко, по-русски, только по
фактам из докладов. Факты из базы знаний подписывай источником
(источник: <source>). Ничего не выдумывай. Если докладов нет, ответь
на сообщение сам.
"""


def build_editor(session):
    return llm.build_agent(f"редактор ({session.login})", EDITOR_PROMPT)


def editor_task(message: str, runs: list[dict]) -> str:
    lines = [f"Сообщение абонента: {message}", "Доклады агентов:"]
    for i, run in enumerate(runs, 1):
        title = CATALOG.get(run["agent"], {}).get("title", run["agent"])
        lines.append(f"{i}. {title}: {run['report']}")
    if not runs:
        lines.append("нет, агенты не запускались.")
    lines.append("Напиши ответ абоненту.")
    return "\n".join(lines)
