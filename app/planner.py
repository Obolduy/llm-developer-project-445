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
    steps: list[Step] = Field(default_factory=list)
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
  list_my_charges.
- kb: база знаний (правила списаний, смены тарифа, услуги, роуминг,
  FAQ). Инструмент: search_kb, в докладе указывает источник.

Правила:
- вопрос о данных абонента — один шаг data, даже если вопросов несколько;
- вопрос о правилах и последствиях — шаг kb;
- «почему списание» — два шага: data (факты) и kb (правила);
- названия тарифов в task пиши строчными без кавычек: базовый, семейный;
- данных для плана мало — заполни question и оставь steps пустыми;
- агенты не нужны (например, приветствие) — пустые steps.
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


async def next_plan(planner, history: list, message: str) -> tuple[Plan, list]:
    """Один вызов планировщика по истории диалога → (план, новая история)."""
    from agents import Runner

    result = await Runner.run(planner, [*history, {"role": "user", "content": message}])
    return result.final_output, result.to_input_list()
