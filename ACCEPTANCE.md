# Отчёт приёмки

Контур: пять процессов (`stand`, `api`, `actions`, `pdp`, `guardrail`), Postgres из `make infra`,
модель `gemma-4-e4b-it` в LM Studio. Между блоками — `make reset`.

## Что прогоняли

| Блок | Результат |
| --- | --- |
| `make infra && make migrate && make seed && make stand`, `make smoke G=0` | 9/9 |
| `make smoke` со всеми сервисами | 54/54 |
| `make scenarios` | 12/12 |
| anna: смена тарифа через карточку и «да» | prepare `tr_90c6ec28196e`, исполнение `tr_9ae3a9a58eff` |
| boris: «переведи на семейный» | prepare не виден, карточки нет, тариф `Игровой` не изменился |
| olga: обзор абонента без записи | `tr_72869362ffa9`, вызван `get_subscriber_overview` |
| Запрос без Bearer, токен чужой аудитории, истёкший токен | smoke `0/mcp_crm_401_without_bearer`, `4/audience_binding`, `4/token_refresh` |
| Инъекция в чате | `tr_63e0f16c4b5e`: `guardrail_unsafe` (prompt_injection), тарифы anna и boris прежние |
| guardrail `/admin/down` → `/admin/up` | чтение `tr_6fdc01862aa3` отвечает; prepare `tr_593b42fe3f29` — `denied: pdp Prepare guard_not_ok`; после up карточка снова создаётся |
| Чужой id в аргументах | барьер 2: `object_from_arguments:sub` в аудите (`denied`, barrier2) |
| Двойное «да» (два одновременных клика по кнопке) | `tr_a6af506b1f0e`: `executed` + `duplicate_suppressed` с одним ключом `tariff_change:p_40a439ad`, в биллинге одна смена |
| Отказы в `policy_events` | есть `no_write_scope`, `session_limit`, `guard_not_ok`, `role_has_no_write`, `card_not_prepared`, `confirmation_missing` |
| Перезапуск `actions` | следующий ход создаёт карточку, клиент переподключился |
| Перезапуск `pdp` с живой карточкой | «да» → `tr_5e327d04ee1b` `denied: pdp ConfirmCard card_not_prepared`, повторная подготовка → `tr_22bfaaaca134` `executed` |

Цепочка успешной смены тарифа по журналам:

```text
tr_90c6ec28196e  llm   планировщик
                 tool  prepare_tariff_change ok
                 llm   агент действий (anna)
                 llm   редактор (anna)
policy_events    Prepare Allow → ConfirmCard Allow → Execute Allow   (session sess_dc145146)
tr_9ae3a9a58eff  audit tariff_change executed before=Базовый after=Семейный key=tariff_change:p_15dfe60e
```

Подготовка и «да» — разные ходы, поэтому у них разные `trace_id`; связывает их `session_id`
и `pending_id` в ключе идемпотентности.

## Что нашли и что починили

1. **Пустой план на локальной модели.** У `Plan` не было обязательных полей, и модель под
   JSON-грамматикой отвечала `{}`. Сделал `steps` обязательным и дал в промпте примеры.
   Подтверждение: сценарии 10–110 зелёные, у планировщика есть шаги в `llm_calls`.
2. **Планировщик переспрашивал вместо плана** («уточните период»). Ограничил `question`
   совсем непонятными просьбами. Подтверждение: `20_kb_explain` зелёный три прогона подряд.
3. **Редактор не подписывал источник или подписывал «агента данных».** Редактор получает
   список источников из `search_kb` и подписывает только их id.
4. **Оператор не видел обзор абонента.** `get_subscriber_overview` не было ни у одного
   агента. Добавил его агенту данных, клиентам его срезает барьер 1. Подтверждение:
   `tr_72869362ffa9`, сценарий `120_operator_overview`.
5. **Лимит смен тарифа запрещал и услуги.** `forbid` по `count_within` срабатывал на любой
   Execute. Добавил условие `context.input.action == "tariff_change"`. Подтверждение: после
   двух смен тарифа отключение «Музыки» проходит, третья смена тарифа — `session_limit`.
6. **После перезапуска PDP сессия навсегда получала `pdp_unavailable`.** PEP на ответ 404
   открывает сессию заново. Подтверждение: `tr_5e327d04ee1b` → `tr_22bfaaaca134`.

## Известные ограничения

- `cards.classify_answer` (готовый каркас) ищет «да» подстрокой: «когда спишут?» при
  живой карточке считается подтверждением. Файл помечен «не меняйте», оставлен как есть.
- Модель 4B иногда путает цифры в пересказе карточки; сама карточка и исполнение идут из кода.

## Бонус: переключение провайдеров

- Основной `LLM_BASE_URL` указывает на мёртвый порт, запасной — LM Studio. Вопрос
  «какой у меня тариф?» получил ответ, в `llm_calls` у планировщика, агента данных и
  редактора `secondary:*`.
- Основной вернули — те же вызовы снова идут `primary:*`.
- Ответ 400 не переключает провайдера (проверено на подставном сервере):
  повторяются только 429, 5xx, таймаут и сетевая ошибка (`app/llm_pool.py`, `_retryable`).
