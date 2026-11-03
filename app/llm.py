"""Сборка агентов на модели из .env + учёт LLM-вызовов.

output_type подключается нестрогой схемой: строгий JSON-режим слишком
требователен к малым моделям.

Каждый запуск агента пишется в llm_calls: модель, токены, задержка.

С пулом провайдеров (бонус failover) в llm_calls пишется фактический
провайдер и его модель: agent = "<провайдер>:<агент>".
"""
from __future__ import annotations

import json
import time

from agents import Agent, AgentOutputSchema, Runner

from core import config, db


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


async def run_logged(agent: Agent, run_input, *, trace_id: str = ""):
    """Runner.run + строка в llm_calls (токены из usage, задержка вокруг запуска)."""
    from app import llm_pool

    box = llm_pool.tracking()
    started = time.perf_counter()
    result = await Runner.run(agent, run_input)
    provider = box.get("provider")
    name = f"{provider.name}:{agent.name}" if provider else agent.name
    usage = result.context_wrapper.usage
    db.execute(
        "insert into llm_calls(trace_id, agent, model, input_tokens, output_tokens, latency_ms)"
        " values (%s, %s, %s, %s, %s, %s)",
        (trace_id or None, name, provider.model if provider else config.MODEL_NAME, usage.input_tokens or 0,
         usage.output_tokens or 0, int((time.perf_counter() - started) * 1000)),
    )
    return result


async def run_agent(agent: Agent, task: str, *, trace_id: str = "") -> dict:
    """Один запуск агента → {"report", "tools": [{"tool", "args", "result"}], "seconds"}."""
    started = time.perf_counter()
    result = await run_logged(agent, task, trace_id=trace_id)
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
