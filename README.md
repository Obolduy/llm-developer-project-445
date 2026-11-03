# AI-оператор для сайта мобильного оператора связи

[![hexlet-check](https://github.com/Obolduy/llm-developer-project-445/actions/workflows/hexlet-check.yml/badge.svg)](https://github.com/Obolduy/llm-developer-project-445/actions)

Соберите агентный чат для сайта мобильного оператора. Он отвечает абоненту на вопросы о тарифе, списаниях и правилах со ссылками на базу знаний, а смену тарифа, услуг и контактов проводит через карточку подтверждения. По пути освоите оркестрацию нескольких агентов на OpenAI Agents SDK, MCP-серверы с JWT, контракты действий с идемпотентностью, журнал аудита, права через политики Dogwood и guardrail-сервис, при отказе которого действия закрываются.

Учебный проект Хекслета: https://ru.hexlet.io/programs/llm-developer
Как это должно работать: https://files.hexlet.app/a/i3355x

## Стек

- Python 3.12, OpenAI Agents SDK, MCP (streamable HTTP), FastAPI
- PostgreSQL 16 + pgvector, Dogwood (политики), JWT RS256

## Установка

```bash
git clone https://github.com/Obolduy/llm-developer-project-445.git
cd llm-developer-project-445
python3.12 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env   # LLM_BASE_URL, LLM_MODEL, LLM_API_KEY; PG_PORT/PG_DSN, если 5434 занят
make infra && make migrate && make seed
```

## Использование

Каждый процесс в своём терминале:

```bash
make stand       # :8100 стенд, веб-чат http://127.0.0.1:8100
make actions     # :8220 action-MCP
make pdp         # :9100 политики Dogwood
make guardrail   # :8300 guardrail
make api         # :8000 бэкенд с оркестратором
```

Проверки: `make smoke` (без модели), `make scenarios` (на живой модели), `make reset` —
вернуть демо-данные. Отчёт приёмки — [ACCEPTANCE.md](ACCEPTANCE.md).

---

<details>
<summary>Автоматические тесты Хекслета</summary>

Тесты запускаются на каждый коммит. За запуск отвечает файл `.github/workflows/hexlet-check.yml` — не удаляйте и не переименовывайте ни его, ни репозиторий.

</details>

## О Хекслете

[Хекслет](https://ru.hexlet.io/) — школа программирования: авторские программы обучения с практикой, поддержкой наставников и реальными проектами, которые остаются в резюме. Этот репозиторий — один из таких проектов.
