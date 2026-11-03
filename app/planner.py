"""Планировщик: план запусков доменных агентов до всякого исполнения.

План — структурированный выход LLM, но исполнению он не принадлежит:
гейт оркестратора оставляет только шаги известных агентов. Действия
(смена тарифа, услуги, контакты, тикет) идут через карточку подтверждения
и политику прав, а не напрямую.
"""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

READ_AGENTS = ("data", "kb")
ACTION_AGENTS = ("tariff", "tickets")


class Step(BaseModel):
    agent: str = Field(
        validation_alias=AliasChoices("agent", "agentName"),
        description="id агента из каталога",
    )
    task: str = Field(
        validation_alias=AliasChoices("task", "job"),
        description="задача агенту одной фразой по-русски, с точными значениями",
    )
    why: str = Field(default="", description="зачем нужен шаг, коротко")
    action: bool = Field(default=False, description="true, только если шаг меняет данные")


class Plan(BaseModel):
    # steps обязателен: без required малые модели под JSON-грамматикой отвечают «{}»
    steps: list[Step] = Field(description="шаги плана; пустой список, если агенты не нужны")
    question: str | None = Field(
        default=None, description="уточняющий вопрос абоненту, если для плана не хватает данных"
    )


PLANNER_PROMPT = """\
Ты планировщик в чате мобильного оператора. С абонентом ты не говоришь:
по его сообщению составь план, каких агентов запустить и с какой задачей.
Ответ строго по схеме Plan, без текста вокруг.

Агенты:
- data: данные абонента (профиль, тариф, баланс, каталог тарифов,
  списания). Инструменты: get_my_profile, get_my_tariff, list_tariffs,
  list_my_charges; оператору ещё get_subscriber_overview — обзор
  абонента по id.
- kb: база знаний (правила списаний, смены тарифа, услуги, роуминг,
  FAQ). Инструмент: search_kb, в докладе указывает источник.
- tariff: действия с тарифом, услугами и контактами. Готовит изменение
  и возвращает карточку подтверждения; сравнивает тарифы.
- tickets: обращение живому оператору (расторжение, жалобы, всё вне тем
  оператора, просьба позвать человека).

Правила:
- вопрос о данных абонента — один шаг data, даже если вопросов несколько;
- оператор просит обзор абонента по id — шаг data, id в task;
- вопрос о правилах и последствиях — шаг kb;
- «почему списание» — два шага: data (факты) и kb (правила);
- просьба сменить тариф, включить или отключить услугу, обновить
  телефон или почту — шаг tariff с action=true, в task прямая команда,
  например «сменить тариф на семейный»;
- сравнение тарифов или «что будет, если перейти» — tariff с action=false;
- запрос вне тем оператора или просьба позвать человека — tickets
  с action=true;
- названия тарифов в task пиши строчными без кавычек: базовый, семейный;
- question — только если просьба совсем непонятна; данные абонента
  (тариф, баланс, списания) агенты достанут сами, о них не переспрашивай;
- агенты не нужны (например, приветствие) — пустые steps.

Пример ответа на «какой у меня тариф и почему списали 440?»:
{"steps": [
  {"agent": "data", "task": "узнать тариф и последние списания абонента", "action": false},
  {"agent": "kb", "task": "правила списания абонентской платы", "action": false}
], "question": null}

Пример ответа на «покажи обзор абонента u_9b1c»:
{"steps": [
  {"agent": "data", "task": "обзор абонента u_9b1c через get_subscriber_overview", "action": false}
], "question": null}

Пример ответа на «переведи меня на игровой»:
{"steps": [
  {"agent": "tariff", "task": "сменить тариф на игровой", "action": true}
], "question": null}
"""


def build_planner():
    from app import llm

    return llm.build_agent("планировщик", PLANNER_PROMPT, output_type=Plan)


def gate(plan: Plan) -> tuple[Plan, list[str]]:
    """Шаги неизвестных агентов отбрасываются, у read-агентов снимается action."""
    kept, dropped = [], []
    for step in plan.steps:
        if step.agent in READ_AGENTS:
            kept.append(step.model_copy(update={"action": False}))
        elif step.agent in ACTION_AGENTS:
            kept.append(step)
        else:
            dropped.append(f"{step.agent}: нет в каталоге")
    return Plan(steps=kept, question=plan.question), dropped


async def next_plan(planner, history: list, message: str, *, trace_id: str = "") -> tuple[Plan, list]:
    """Один вызов планировщика по истории диалога → (план, новая история)."""
    from app import llm

    result = await llm.run_logged(planner, [*history, {"role": "user", "content": message}], trace_id=trace_id)
    return result.final_output, result.to_input_list()
