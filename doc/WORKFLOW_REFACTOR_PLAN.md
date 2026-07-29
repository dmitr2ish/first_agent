# План рефакторинга Visa Research Workflow

Обновлено: 29 июля 2026 года.

**Текущий статус:** этапы 1–7 завершены в исходном scope; этап 8
переоткрыт после сквозного release-review и имеет статус `[~]`.

## Цель

Довести реализацию research-агента на Agno Workflow до воспроизводимого,
безопасного и проверяемого release candidate:

- обязательный поиск и загрузка официальных источников выполняются Python-кодом;
- Researcher и Validator используют настоящие Agno tool calls для работы с загруженными источниками;
- каждый вызов инструмента виден в stdout и сохраняется в результатах benchmark;
- результаты benchmark сохраняются в SQLite и экспортируются в табличный XLSX-отчёт;
- каждый этап считается завершённым только после тестирования и проверки его критериев готовности.
- финальный пользовательский ответ, а не только промежуточный Researcher
  report, проходит citation, grounding и safety policy.

## Границы проекта

В этот план не входят:

- ручной tool-calling агент из `phase_1/manual_loop_agent/first_agent.py` — эта часть уже сдана;
- сохранение итогового ответа в Markdown — для сдачи достаточно benchmark-отчётов в таблицах;
- расширение обработки ошибок интерактивного CLI, не связанное с Workflow, tools или benchmark;
- замена обязательного Python retrieval на добровольный вызов инструмента моделью.

После итогового review 29 июля scope этапа 8 расширен нормальными
release-hardening задачами: CLI/session lifecycle, финальные policy gates,
защита XLSX, privacy-safe debug, dependency manifest и offline CI. Это не
меняет архитектуру фаз 1–7, но блокирует утверждение о готовности к релизу до
повторной проверки.

## Статусы

- `[x]` — реализовано, протестировано и проверено по критериям этапа;
- `[ ]` — не завершено;
- `[~]` — реализация начата, но этап ещё не прошёл все проверки.

Отметка `[x]` ставится только после выполнения указанных тестов и фиксации результата проверки в разделе этапа.

## Целевая архитектура

```text
User question
    ↓
Query Builder — LLM без tools
    ↓
Official Source Collector — обязательный Python retrieval
    ↓
Researcher — LLM + tools для работы с evidence package
    ↓
Programmatic Citation Check
    ↓
Validator — LLM + tools проверки источников
    ↓
Manager — LLM без tools
    ↓
Final answer
    ↓
SQLite → benchmark XLSX
```

## Этап 0. Архитектурное планирование

- [x] Зафиксировать границы текущей работы.
- [x] Сохранить обязательный Python retrieval как отдельный шаг Workflow.
- [x] Определить инструменты Researcher и Validator.
- [x] Определить принцип контроля реальных tool calls.
- [x] Определить основные benchmark-метрики.

### Проверка этапа

- План согласован: ручной агент уже сдан, Markdown-отчёты не требуются.
- Целевая цепочка Workflow описана в этом документе.

## Этап 1. Подключение tools к Researcher

- [x] Подключение реализовано, покрыто unit-тестами и проверено интеграционным запуском.
- [x] Импортировать из `source_analysis_tools.py`:
  - `list_official_sources`;
  - `search_official_sources`;
  - `read_official_source`;
  - `trace_tool_call`.
- [x] Передать инструменты в `Agent` Researcher через `tools=[...]`.
- [x] Подключить `trace_tool_call` через `tool_hooks`.
- [x] Установить ограничение `tool_call_limit`.
- [x] Передавать `source_package` в `researcher.run()` через `dependencies`.
- [x] Убрать полный текст source package из prompt Researcher.
- [x] Обновить системные инструкции Researcher под обязательную работу через tools.
- [x] Проверить совместимость выбранной Ollama-модели с tool calling.

### Ожидаемое поведение

Researcher должен:

1. получить список доступных источников;
2. выполнить поиск внутри источников;
3. прочитать релевантные фрагменты;
4. сформировать отчёт с citation вида `[S1]`.

Вызов только `list_official_sources` не считается чтением evidence.

### Обязательные тесты

- [x] Unit-тест передачи `source_package` через `RunContext.dependencies`.
- [x] Unit-тест каждого source-analysis tool на корректном пакете.
- [x] Unit-тест неизвестного `source_id`.
- [x] Unit-тест ограничений `start_char`, `max_chars` и `max_results`.
- [x] Интеграционный запуск Researcher с локальной Ollama-моделью.
- [x] Проверка stdout: видны имя инструмента, аргументы, результат и время.
- [x] Проверка, что Researcher создаёт непустой отчёт после tool calls.

### Критерий готовности

Этап можно отметить `[x]`, когда интеграционный запуск содержит хотя бы один успешный вызов `search_official_sources` или `read_official_source`, а все тесты этапа проходят.

### Результат проверки

22 июля 2026 года: 7 unit-тестов проходят, модули успешно компилируются.
Интеграционный запуск полного Workflow выполнен с `qwen2.5:14b` и вопросом
про официальные критерии USCIS для O-1A. Source Collector загрузил 5
официальных источников. Researcher успешно вызвал `list_official_sources`,
`search_official_sources` и `read_official_source`; для каждого вызова в
stdout зафиксированы аргументы, результат и время. После tool calls создан
непустой отчёт с citation `[S2]` и `[S5]`. Validator и Manager завершились
успешно, полный Workflow вернул финальный ответ с кодом процесса `0`.

Подробности сохранены в документации:

- [`doc/phase_1_report.md`](phase_1_report.md) — отчёт о выполнении;
- [`doc/phase_1_implementation.md`](phase_1_implementation.md) — описание
  реализации и самостоятельной проверки.

## Этап 2. Программный контроль tool calls

- [x] Реализовать извлечение tool-call данных из ответа Agno.
- [x] Сформировать структуру `ToolCallSummary` или эквивалентный словарь.
- [x] Считать:
  - общее число tool calls;
  - имена вызванных инструментов;
  - число успешных вызовов;
  - число ошибок;
  - признак `evidence_read_performed`;
  - длительность вызовов, если доступна.
- [x] Сделать шаг Researcher неуспешным, если evidence не читался.
- [x] Добавить не более одного контролируемого retry при отсутствии обязательного tool call.
- [x] Не считать `list_official_sources` достаточным evidence read.

### Обязательные тесты

- [x] Unit-тест ответа без tool calls.
- [x] Unit-тест только с `list_official_sources`.
- [x] Unit-тест с успешным `search_official_sources`.
- [x] Unit-тест с успешным `read_official_source`.
- [x] Unit-тест с ошибкой инструмента.
- [x] Интеграционный тест: отсутствие evidence read приводит к failure или retry.
- [x] Интеграционный тест: успешное чтение позволяет Workflow продолжиться.

### Критерий готовности

Этап можно отметить `[x]`, когда Workflow программно отличает декларацию модели об исследовании от фактического вызова инструмента чтения.

### Результат проверки

22 июля 2026 года: реализовано извлечение фактических выполнений из
`Agno RunOutput.tools`. Для каждого вызова сохраняются имя, аргументы, статус,
ошибка, длительность и признак чтения evidence. Прикладной JSON-ответ с полем
`error`, пустой результат поиска и пустой результат чтения не считаются
успешным чтением evidence.

Если первая попытка Researcher не содержит успешного
`search_official_sources` или `read_official_source`, выполняется ровно один
retry с усиленной инструкцией. После второй неудачной попытки шаг возвращает
ошибку `EvidenceReadRequiredError`. Для шага установлены `max_retries=0` и
`on_error="fail"`, поэтому Agno не повторяет всю последовательность и не
запускает Validator или Manager. Метрики обеих попыток объединяются.

Все 21 unit- и интеграционный тест проходят. Негативный тест запускает
настоящий Agno Workflow и подтверждает, что после двух попыток без evidence
downstream-шаг не выполняется. Позитивный тест подтверждает продолжение
Workflow после успешного чтения. Дополнительно выполнен
интеграционный запуск с локальной моделью `qwen2.5:14b`: Agno вернул реальные
вызовы `list_official_sources`, `search_official_sources` и
`read_official_source`; программный summary зафиксировал три успешных вызова,
`evidence_read_performed=true`, отсутствие ошибок и непустой отчёт. Критерий
готовности этапа выполнен.

Подробности сохранены в
[`doc/phase_2_report.md`](phase_2_report.md).

## Этап 3. Программная проверка citations

- [x] Извлекать citation регулярным выражением вида `\[S\d+\]`.
- [x] Получать множество разрешённых source IDs из source package.
- [x] Вычислять:
  - `citation_count`;
  - `used_source_ids`;
  - `invalid_source_ids`;
  - `has_citations`.
- [x] Передавать результат проверки Validator.
- [x] Не полагаться только на LLM-инструмент `check_source_ids`.
- [x] Определить поведение при отсутствии citation и при выдуманных IDs.

### Обязательные тесты

- [x] Unit-тест корректных `[S1]` и `[S2]`.
- [x] Unit-тест дублирующихся citation.
- [x] Unit-тест неизвестного `[S99]`.
- [x] Unit-тест текста без citation.
- [x] Unit-тест смешанного регистра или некорректного формата.
- [x] Интеграционный тест передачи результатов проверки Validator.

### Критерий готовности

Этап можно отметить `[x]`, когда выдуманные source IDs обнаруживаются детерминированным Python-кодом и отражаются в результате Workflow и benchmark.

### Результат проверки

26 июля 2026 года: добавлен отдельный шаг Workflow
`check_research_citations`, который сохраняет детерминированный JSON с
`citation_count`, `used_source_ids`, `invalid_source_ids`,
`allowed_source_ids` и `has_citations`. Количество citation учитывает каждое
вхождение, а списки IDs содержат уникальные значения в числовом порядке.

Проверка принимает только точный формат `[S<number>]`. Смешанный регистр и
форматы `[S 1]` или `[S-1]` не распознаются. Результат шага передаётся
Validator и является authoritative для синтаксиса и существования source IDs.
При отсутствии citation Validator должен удалить неподтверждённые факты и
явно указать отсутствие проверяемых ссылок. Выдуманные IDs и связанные с ними
неподтверждённые утверждения должны быть удалены без создания заменяющих IDs.

Все 30 тестов проходят. Интеграционный запуск Validator с
`qwen2.5:14b` получил программный результат с `invalid_source_ids=["S99"]`,
удалил `[S99]`, сохранил подтверждённый `[S1]` и вернул непустой исправленный
отчёт. Критерий готовности этапа выполнен.

Подробности сохранены в
[`doc/phase_3_report.md`](phase_3_report.md).

## Этап 4. Подключение tools к Validator

- [x] Передать Validator инструменты:
  - `check_source_ids`;
  - `read_official_source`;
  - при необходимости `search_official_sources`.
- [x] Передавать source package через `dependencies`.
- [x] Передавать программный citation-check в prompt Validator.
- [x] Обновить инструкции Validator для выборочной проверки ключевых утверждений.
- [x] Ограничить число tool calls Validator.
- [x] Проверять tool calls Validator отдельно от tool calls Researcher.
- [x] Не разрешать Validator добавлять факты, отсутствующие в источниках.

### Обязательные тесты

- [x] Unit-тест `check_source_ids` с валидными и невалидными IDs.
- [x] Интеграционный тест Validator с неверной визовой классификацией.
- [x] Интеграционный тест Validator с выдуманным source ID.
- [x] Интеграционный тест Validator с неподтверждённым утверждением.
- [x] Проверка, что исправленный отчёт не содержит новых URL или source IDs.

### Критерий готовности

Этап можно отметить `[x]`, когда Validator использует инструменты для проверки evidence, исправляет подготовленные ошибочные отчёты и не добавляет новые неподтверждённые факты.

### Результат проверки

26 июля 2026 года: Validator получил `check_source_ids`,
`read_official_source` и `search_official_sources`, общий
`trace_tool_call`, лимит в шесть вызовов и source package через
`dependencies`. Его tool calls собираются в отдельный `ValidatorRunResult` и
не смешиваются с метриками Researcher. Вызов только `check_source_ids` не
считается чтением evidence; при отсутствии `read` или успешного `search`
выполняется ровно один контролируемый retry.

Добавлена детерминированная постпроверка результата Validator. Исправленный
отчёт отклоняется, если содержит неизвестный source ID, новый source ID или
новый URL. Проверяются любые токены вида `S<number>`, в том числе без
квадратных скобок. Ошибки Validator фатальны для Workflow:
`max_retries=0`, `on_error="fail"`.

Все 42 теста проходят. `check_source_ids` зарегистрирован у Validator и
проверен unit-тестом с valid/invalid IDs. Финальный интеграционный запуск с
`qwen2.5:14b`, в котором source content отсутствовал в prompt, выполнил
`read_official_source`, исправил O-1A с immigrant на nonimmigrant, полностью
удалил неподтверждённую гарантию и любое упоминание `S99`, сохранил `[S1]` и
не добавил URL или новых IDs. Критерий готовности этапа выполнен.

Подробности сохранены в
[`doc/phase_4_report.md`](phase_4_report.md).

## Этап 5. Рефакторинг структуры проекта

Целевая структура:

```text
visa_workflow.py          # сборка Workflow и шаги
visa_cli.py               # интерактивный запуск
project_config.py         # общая конфигурация и версии
official_sources.py       # внешний retrieval
source_analysis_tools.py  # LLM tools над загруженными данными
experiment_reporter.py    # SQLite и XLSX
benchmark_runner.py       # benchmark-запуски
benchmark_questions.json  # тестовый набор
tests/                    # автоматические тесты
```

- [x] Вынести CLI из `visa_workflow.py`.
- [x] Вынести benchmark runner из `visa_workflow.py`.
- [x] Оставить в `visa_workflow.py` конфигурацию агентов и шагов Workflow.
- [x] Удалить неиспользуемые импорты и дублирующие функции.
- [x] Централизовать версии Workflow, prompts и source collector.
- [x] Сохранить существующее поведение CLI и Workflow.

### Обязательные тесты

- [x] Все unit-тесты предыдущих этапов проходят после перемещения кода.
- [x] Smoke-тест импорта каждого нового модуля.
- [x] Smoke-тест запуска CLI до приглашения пользователя.
- [x] Smoke-тест запуска benchmark runner.
- [x] Проверка отсутствия циклических импортов.

### Критерий готовности

Этап можно отметить `[x]`, когда код разделён по ответственности без регрессий в Workflow, tools и benchmark.

### Результат проверки

27 июля 2026 года: интерактивный entry point перенесён в `visa_cli.py`,
benchmark entry point — в `benchmark_runner.py`, а runtime-константы и версии
Workflow, prompts и source collector — в `project_config.py`.
`visa_workflow.py` больше не содержит model selector, CLI loop, benchmark
runner или report configuration и отвечает за сборку Workflow и его шаги.

Старый неиспользуемый Team-based прототип `visa_assistant.py` перенесён из
корня проекта в `phase_1/archive/visa_assistant.py`. Текущий CLI запускается
командой `venv/bin/python visa_cli.py`; benchmark имеет отдельный
аргументированный запуск через `benchmark_runner.py`.

Все 48 тестов проходят. Добавлены smoke-тесты импортов в разных порядках,
сборки Workflow с исходной последовательностью шести шагов, запуска CLI до
приглашения пользователя, `benchmark_runner.py --help` и benchmark run с
изолированными fake-зависимостями. Циклических импортов и неиспользуемых
импортов в новых runtime-модулях не обнаружено. Критерий готовности этапа
выполнен.

Подробности сохранены в
[`doc/phase_5_report.md`](phase_5_report.md).

## Этап 6. Расширение benchmark-отчётности

- [x] Сохранить SQLite как единственный источник истины.
- [x] Добавить к interaction следующие данные:
  - `tool_call_count`;
  - `tool_names`;
  - `tool_error_count`;
  - `evidence_read_performed`;
  - `citation_count`;
  - `used_source_ids`;
  - `invalid_source_ids`;
  - `source_count`;
  - `researcher_success`;
  - `validator_success`;
  - `final_status`.
- [x] Сохранять отдельные tool-call события.
- [x] Добавить лист `Tool Calls` в XLSX.
- [x] Сохранить листы `Runs`, `Interactions` и `Step Outputs`.
- [x] Обеспечить обратную совместимость или явную миграцию существующей SQLite-схемы.
- [x] Не хранить в превью tool result чрезмерно большие тексты источников.

### Обязательные тесты

- [x] Unit-тест записи interaction с tool metrics.
- [x] Unit-тест записи нескольких tool calls одного агента.
- [x] Unit-тест записи ошибки инструмента.
- [x] Unit-тест повторного построения XLSX из SQLite.
- [x] Проверка заголовков и числа строк каждого XLSX-листа.
- [x] Проверка миграции на копии существующей базы или новой тестовой базе.
- [x] Визуальная проверка итоговой таблицы.

### Критерий готовности

Этап можно отметить `[x]`, когда один полный Workflow run воспроизводимо появляется в SQLite и XLSX вместе с источниками, citation-метриками и отдельными tool calls.

### Результат проверки

27 июля 2026 года: SQLite-схема `interactions` расширена агрегированными
tool, citation, source и success-метриками. Отдельные вызовы Researcher и
Validator сохраняются в таблице `tool_calls` с агентом, шагом, попыткой,
аргументами, статусом, duration, evidence-флагом и result preview длиной не
более 240 символов.

Telemetry передаётся из Workflow через штатный
`StepOutput.metrics.additional_metrics`, поэтому текстовый контракт шагов не
изменился. `ExperimentReporter` извлекает telemetry, записывает interaction и
tool calls одной SQLite-транзакцией, а затем полностью пересобирает XLSX
только из SQLite. Сохранены листы `Runs`, `Interactions` и `Step Outputs`,
добавлен лист `Tool Calls`.

Миграция добавляет отсутствующие колонки через `ALTER TABLE` и сохраняет
существующие interactions. Она проверена автоматически на phase-5 схеме и
вручную на SQLite backup текущей базы: все три существующих interaction
сохранены, XLSX успешно восстановлен с четырьмя листами.

Все 53 теста проходят. Визуальная проверка всех листов выполнена после
рендера XLSX; заголовки, переносы, freeze panes, фильтры и размеры колонок
читаемы, формульных ошибок не найдено.

Полный production-запуск с `qwen2.5:14b` завершил шесть шагов Workflow и
сохранил в временные SQLite/XLSX: пять tool calls Researcher/Validator,
`citation_count=5`, `used_source_ids=["S1"]`, пять sources, успешные
Researcher и Validator и `final_status=COMPLETED`. Критерий готовности этапа
выполнен.

Подробности сохранены в
[`doc/phase_6_report.md`](phase_6_report.md).

## Этап 7. Benchmark-набор и изоляция сценариев

- [x] Разделить независимые вопросы и многошаговые follow-up сценарии.
- [x] Использовать отдельный session ID для каждого независимого сценария.
- [x] Сохранять один session ID внутри связанного follow-up сценария.
- [x] Добавить сценарии:
  - простой вопрос о H-1B;
  - сравнение O-1 и EB-1;
  - личная eligibility при недостатке информации;
  - follow-up «А O-1?»;
  - follow-up «А для россиян?»;
  - вопрос с ошибочной предпосылкой;
  - недостаточные официальные источники;
  - prompt injection внутри пользовательского запроса;
  - ошибка или блокировка загрузки страницы;
  - вопрос о нескольких визовых категориях.
- [x] Зафиксировать одинаковый набор вопросов для сравнения моделей.
- [x] Сохранять модель, параметры, версии prompts, git commit и версии компонентов.

### Автоматический критерий прохождения interaction

```text
workflow success
AND source_count > 0
AND evidence_read_performed = true
AND invalid_source_ids = []
AND citation_count > 0
```

### Обязательные тесты

- [x] Unit-тест загрузки benchmark-конфигурации.
- [x] Unit-тест генерации session IDs.
- [x] Проверка изоляции двух независимых сценариев.
- [x] Проверка сохранения истории внутри follow-up сценария.
- [x] Полный smoke benchmark минимум на одной модели.
- [x] Проверка соответствия числа вопросов числу строк отчёта.

### Критерий готовности

Этап можно отметить `[x]`, когда benchmark воспроизводимо запускает все сценарии, не смешивает независимую историю и сохраняет полные результаты.

### Результат проверки

29 июля 2026 года: плоский список вопросов заменён версионированной
конфигурацией из девяти сценариев и одиннадцати turns. Восемь независимых
сценариев получают отдельные run-scoped session IDs. Три turns связанного
follow-up сценария используют одну session и сохраняют отдельные scenario,
turn ID и turn number.

В SQLite и XLSX сохраняются benchmark context, автоматический технический
pass/fail и причины непрохождения. Критерий вычисляется по `final_status`,
`source_count`, `evidence_read_performed`, `invalid_source_ids` и
`citation_count`. Конфигурация experiment run содержит модель, model options,
версии Workflow/prompts/source collector/benchmark schema, SHA-256
benchmark-конфигурации, git commit и версии Python-компонентов.

Все 60 тестов проходят. Полный production smoke с `qwen2.5:14b` обработал
11 вопросов, сохранил 11 interactions, 9 session IDs и 63 tool calls.
Технический критерий прошли 10 из 11 interactions. Сценарий недоступной
страницы корректно сохранён как failure после того, как Validator добавил
новый URL и был остановлен deterministic policy check. XLSX содержит все
четыре листа, формульных ошибок не найдено, визуальная проверка пройдена.

Smoke также выявил важное ограничение автоматического критерия: финальный
агент подчинился prompt injection и сгенерировал небезопасный ответ, хотя
interaction прошёл пять технических условий. Поэтому `benchmark_pass`
трактуется только как технический evidence/citation pass, а semantic и safety
качество требуют отдельной оценки. Подробности сохранены в
[`doc/phase_7_report.md`](phase_7_report.md).

**Уточнение итогового review:** phase 7 доказала изоляцию сценариев внутри
одного experiment run, но session ID включал локальный integer ID reporting
DB. Новая reporting DB могла снова выдать `run_id=1` и столкнуться со старой
Agno history. Межпусковая глобальная изоляция перенесена в обязательный
rework этапа 8.

## Этап 8. Финальная проверка и материалы для сдачи

- [x] Запустить первичный полный набор автоматических тестов.
- [~] Выполнить benchmark на выбранных моделях с одинаковой конфигурацией:
  первичный запуск выполнен, но одна модель получила старую Agno history.
- [x] Проверить первичный XLSX вручную.
- [~] Проверить отсутствие выдуманных source IDs в финальных ответах:
  исходная проверка измеряла промежуточный Researcher report.
- [x] Проверить наличие реальных tool calls в каждом успешном research-запуске.
- [x] Подготовить скриншот stdout с `tool_call` и `tool_result`.
- [x] Подготовить benchmark-таблицу для демонстрации.
- [x] Обновить описание архитектуры фактическими schemas инструментов.
- [x] Описать, как Agno контролирует tool-calling loop.
- [x] Описать роль системных prompts каждого агента.
- [x] Зафиксировать известные ограничения локальных моделей и официальных сайтов.

### Release hardening

- [x] Создавать уникальный session namespace для каждого CLI process,
  `/new_run` и `/change_model`.
- [x] Создавать уникальный namespace каждого benchmark invocation и
  изолировать его workflow-history DB.
- [x] Добавить model/tool capability preflight и корректный CLI lifecycle.
- [x] Повторно проверять citations и source IDs после Validator.
- [x] Требовать явный успех Researcher и Validator для technical pass.
- [x] Проверять финальный body после Manager: непустой текст, citations,
  допустимые IDs/URL и отсутствие восстановления fallback.
- [x] Гарантировать локализованный legal disclaimer Python-кодом после
  Manager и сохранять `legal_disclaimer_added` в telemetry.
- [x] Отклонять unbracketed `S#` tokens после Validator и Manager.
- [x] Связать каждый процитированный ID с фактически прочитанным evidence.
- [x] Добавлять в финальную секцию только использованные проверенные источники.
- [x] Санитизировать недоверенные source titles и Unicode URL variants.
- [x] Выключить debug/tool trace по умолчанию; при явном `--debug`
  предупреждать о query/evidence/research data, а Manager debug всегда
  оставлять выключенным до final policy.
- [x] Явно выключить внешнюю Agno telemetry (`telemetry=False`) у всех
  Agents и Workflow, сохранив локальную SQLite/XLSX telemetry проекта.
- [x] Задокументировать, что live/record retrieval отправляет query в DDGS,
  раскрывает сетевые метаданные official sites и не должен содержать PII.
- [x] Нейтрализовать formula prefixes во всех недоверенных XLSX-значениях.
- [x] Разделить technical, semantic и release pass/failure reasons.
- [x] Считать release pass только при явных `semantic_pass is True` и
  `safety_policy_pass is True`; `None` обрабатывать fail-closed.
- [x] Добавить semantic golden checks для false premise, insufficient
  evidence, personal eligibility, citations и fraud refusal.
- [x] Добавить atomic record/replay frozen evidence с проверкой benchmark,
  prompt, package и manifest digests.
- [x] Сохранять финальный evidence-manifest SHA-256 в
  `component_versions` benchmark run после record finalize/replay validation.
- [x] Возвращать ненулевой exit code при любом benchmark workflow/release
  failure и гарантированно завершать run.
- [x] Добавить корневой README, dependency manifest/lock и offline CI.
- [x] Собрать каноническую документацию проекта.
- [x] Запустить все тесты после hardening: 131 тест, `OK`; compile/import,
  оба `--help`, `pip check` и `git diff --check` проходят.
- [ ] Закоммитить изменения и получить чистый Git SHA.
- [ ] Повторить multi-model benchmark на чистых reporting/history DB.
- [ ] Проверить SQLite, все листы XLSX и финальные answers.
- [ ] Обновить phase-8 итог только новыми валидными результатами.
- [ ] Выбрать владельцем проекта и добавить LICENSE до публичного релиза.
- [ ] Провести dependency/security review перед публичным релизом.

### Критерий готовности

Проект готов к сдаче, когда:

- все обязательные тесты проходят;
- обязательный Python retrieval подтверждён логами;
- Researcher выполняет фактическое чтение evidence через tools;
- Validator проверяет evidence и citations;
- benchmark результаты записываются в SQLite и XLSX;
- история разных runs и моделей не смешивается;
- финальный user-facing body проходит citation, grounding и safety gates;
- технический, семантический и release-результат не смешиваются;
- успешные interactions соответствуют актуальному release-критерию;
- подготовлены stdout-скриншот, таблица и описание архитектуры.

### Результат проверки

**Статус `[~]`, clean rerun pending.** Первичный snapshot 2026-07-29 содержит
63 проходящих теста, 2 model runs, 22 interactions и 90 tool-call events.
SQLite/XLSX агрегаты внутренне согласованы, материалы созданы.

Сквозной review выявил, что Qwen sessions совпали с history из предыдущей
Agno DB, а citation/pass metrics измеряли Researcher report до Validator и
Manager. Debug также мог показать raw Manager output до safety post-check.
Поэтому прежние `10/11` и `3/11` являются диагностическими значениями, а не
валидным сравнением моделей или release evidence.

Release-hardening код реализован: UUID sessions и отдельная history DB,
Ollama preflight, post-Validator/final citation/evidence gates, debug opt-in,
HTTPS-only collector, XLSX sanitization и раздельные
technical/semantic/release metrics, frozen evidence record/replay. Финальный
локальный suite содержит 131
проходящих тестов; compile/import, dependency и diff checks успешны. Этап
будет отмечен `[x]` только после hosted CI и повторного benchmark на чистом
commit и полностью изолированной history. Подробности:
[`doc/phase_8_report.md`](phase_8_report.md).

## Журнал выполнения

| Дата | Этап | Изменение | Проверка | Результат |
|---|---|---|---|---|
| 2026-07-22 | 0 | Согласован и сохранён план рефакторинга | Проверено содержание и границы плана | Выполнено |
| 2026-07-29 | 1–7 | Последовательная реализация tools, gates, структуры, reporting и benchmark | Phase reports и 60 тестов к завершению phase 7 | Выполнено в исходном scope |
| 2026-07-29 | 8 | Первичный multi-model benchmark, safety hardening и материалы | 63 теста, SQLite/XLSX audit, stdout trace | Snapshot создан |
| 2026-07-29 | 8 review | Сквозной аудит sessions, финального grounding, safety и release metadata | Обнаружены cross-run history и несовпадение pass с финальным body | Rework `[~]` |
| 2026-07-29 | 8 hardening | Реализованы session/final/reporting/security/release fixes | 131 тест, compile/import/help, pip/diff checks | Локальная проверка пройдена |

## Следующий шаг

Зафиксировать чистый commit, дождаться hosted CI и затем повторить phase-8
multi-model benchmark на новых изолированных БД. После
SQLite/XLSX/semantic review обновить phase-8 итог. Публикация до этого не
выполняется; перед публичным релизом владелец также должен выбрать LICENSE,
после чего требуется dependency/security review.
