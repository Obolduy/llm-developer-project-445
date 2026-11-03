"""Сборка агентов на модели из .env + учёт LLM-вызовов.

output_type подключается нестрогой схемой: строгий JSON-режим слишком
требователен к малым моделям.

TODO(аудит): в run_agent — INSERT в llm_calls: trace_id, имя агента,
  модель, токены (result.context_wrapper.usage), латентность.

TODO(failover): если llm_pool.enabled() — запуск через
  llm_pool.run_with_failover, в llm_calls — фактический провайдер.
"""
from __future__ import annotations

import json
import time

from agents import Agent, AgentOutputSchema, Runner

from core import config


def build_agent(name: str, instructions: str, tools=None, output_type=None) -> Agent:
    return Agent(
        name=name,
        instructions=instructions,
        tools=list(tools or []),
        output_type=AgentOutputSchema(output_type, strict_json_schema=False) if output_type else None,
        model=config.MODEL_NAME or config.setup_model(),
    )


def _get(raw, key):
    return raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)


def _loads(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value) if value.strip() else {}
    except ValueError:
        return value


async def run_agent(agent: Agent, task: str, *, trace_id: str = "") -> dict:
    """Один Runner.run → {"report", "tools": [{"tool", "args", "result"}], "seconds"}."""
    started = time.perf_counter()
    result = await Runner.run(agent, task)
    seconds = time.perf_counter() - started

    outputs = {
        _get(item.raw_item, "call_id"): item.output
        for item in result.new_items
        if item.type == "tool_call_output_item"
    }
    tools = [
        {
            "tool": _get(item.raw_item, "name"),
            "args": _loads(_get(item.raw_item, "arguments")) or {},
            "result": _loads(outputs.get(_get(item.raw_item, "call_id"))),
        }
        for item in result.new_items
        if item.type == "tool_call_item"
    ]

    report = result.final_output
    if not isinstance(report, str):
        report = json.dumps(report, ensure_ascii=False, default=str)
    return {"report": report, "tools": tools, "seconds": seconds}
