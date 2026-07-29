# Архитектура Visa Research Workflow

> Этот файл сохраняет компактный архитектурный snapshot первичной реализации
> phase 8. Каноническое актуальное описание, включая выявленные gaps и
> release-hardening target, находится в
> [`PROJECT_DOCUMENTATION.md`](PROJECT_DOCUMENTATION.md).

Документ описывает архитектуру первичного snapshot этапа 8. Главный принцип:
модель предлагает действия и формулирует текст, а обязательные условия
retrieval, evidence, citations, допустимых источников и безопасности
проверяются Python-кодом.

## Поток данных

1. `build_search_query` превращает текущий вопрос и ограниченную историю в
   самостоятельный англоязычный поисковый запрос.
2. `collect_official_sources` выполняет Python-retrieval только по разрешённым
   доменам государственных ведомств США, загружает страницы и присваивает им
   локальные идентификаторы `S1`, `S2`, ...
3. `analyze_official_sources` передаёт пакет источников Researcher через
   Agno `dependencies`. Researcher видит содержимое только через tools.
4. `check_research_citations` детерминированно извлекает ссылки вида `[S1]`,
   сопоставляет их с пакетом и фиксирует недопустимые IDs.
5. `validate_research` повторно читает evidence через tools, удаляет
   неподтверждённые утверждения и проходит программный output-policy check.
6. `create_final_answer` формирует ответ на языке пользователя. URL удаляются
   из модельного текста и добавляются только из пакета источников. Отдельный
   post-check блокирует практические советы по подделке документов,
   доказательств или фактов. Если Manager пропустил legal disclaimer, Python
   добавляет локализованную константу и записывает
   `legal_disclaimer_added`.
7. `ExperimentReporter` сначала записывает run, interaction, step telemetry и
   tool calls в SQLite, затем полностью пересобирает XLSX из SQLite.

У всех Agno Agents и Workflow явно задано `telemetry=False`, поэтому внешняя
framework telemetry отключена. Локальная telemetry `ExperimentReporter`
остаётся частью дизайна. Live retrieval всё равно отправляет generated query
в DDGS и выполняет внешние HTTPS-запросы; он не является offline и не должен
получать PII.

После finalize/validation frozen evidence его финальный manifest SHA-256
записывается reporter-ом в `component_versions` benchmark run, связывая
SQLite provenance с точным cache artifact.

## Фактические schemas source tools

Скрытый параметр `run_context` предоставляет Agno и не выбирается моделью.
Все tools возвращают JSON-строку.

```text
list_official_sources() -> str
```

Возвращает для каждого доступного источника `source_id`, `title`, `url`,
`document_type` и `content_length`.

```text
read_official_source(
    source_id: str,
    start_char: int = 0,
    max_chars: int = 5000,
) -> str
```

Читает фрагмент одного источника. `start_char` ограничивается снизу нулём,
`max_chars` — диапазоном 500–8000. Результат содержит ID, заголовок, URL,
смещение, длины и `content`; неизвестный ID возвращает JSON с `error`.

```text
search_official_sources(
    query: str,
    max_results: int = 6,
) -> str
```

Ищет слова внутри уже загруженных официальных документов. Число результатов
ограничено диапазоном 1–10. Результат содержит запрос и ранжированные excerpts.

```text
check_source_ids(source_ids: list[str]) -> str
```

Возвращает множества `valid`, `invalid` и `available`. Tool используется
Validator, но сам по себе не считается чтением evidence.

## Как Agno и Python контролируют tool-calling loop

Agno передаёт функции tools модели, исполняет выбранные вызовы и сохраняет
фактические события в `response.tools`. Хук `trace_tool_call` печатает
`[🔧 TOOL CALL]`, arguments, `[✅ TOOL RESULT]` или ошибку и длительность.

После каждого ответа Python нормализует `response.tools` в
`ToolCallSummary`: имя, arguments, attempt, success/error, duration,
`evidence_read` и короткий preview. Успешный
`read_official_source` с непустым `content` или
`search_official_sources` с непустым `matches` считается чтением evidence.
`list_official_sources` и `check_source_ids` evidence не заменяют.

Если Researcher или Validator не прочитал evidence, контроллер делает ровно
один повтор с обязательной инструкцией начать с чтения. После второй неудачи
шаг завершается ошибкой. Внешний retry шага для Researcher и Validator
отключён, поэтому скрытых дополнительных циклов нет. Validator дополнительно
не может добавить новый URL, новый source ID или ID вне текущего пакета.

## Роли системных prompts

- **Query Builder** — формирует один самостоятельный официальный поисковый
  запрос и использует историю только для разрешения контекста.
- **Researcher** — анализирует только пакет текущего запуска, обязан вызвать
  evidence tool, цитирует локальные IDs и не трактует текст веб-страниц как
  инструкции.
- **Validator** — сверяет отчёт с фактически прочитанным evidence, принимает
  программный citation check как источник истины для IDs и возвращает только
  исправленный подтверждённый отчёт.
- **Visa Manager** — адаптирует валидированный отчёт к вопросу пользователя,
  сохраняет citations, не добавляет знания или URL и отказывает в содействии
  подделке либо иммиграционному мошенничеству.

System prompts не являются единственной защитой. Python отдельно проверяет
evidence, Researcher citations, часть Validator output policy и узкую
fraud-политику финального ответа. Сквозной review показал, что до релиза
необходимы повторные citation/grounding gates после Validator и Manager.

## Хранилище и воспроизводимость

SQLite — единственный источник истины. Таблицы `experiment_runs`,
`interactions` и `tool_calls` сохраняют конфигурацию, версии, git revision,
идентификаторы сценариев и сессий, результаты шагов, citation metrics и
фактические tool events. XLSX пересоздаётся атомарно и содержит листы
`Benchmark Summary`, `Runs`, `Interactions`, `Step Outputs` и `Tool Calls`.

Одинаковость сравнительного benchmark подтверждается версией набора,
SHA-256 digest конфигурации, версиями workflow/prompts/collector, параметрами
модели и git revision. Незакоммиченное состояние помечается суффиксом
`-dirty`.

## Известные ограничения

- Локальные модели могут ошибаться в выборе tool, аргументах, классификации
  виз и следовании prompt. Программные gates ограничивают последствия, но не
  гарантируют юридическую корректность каждого текста.
- `benchmark_pass` — технический критерий: успешный Workflow, источники,
  evidence read, отсутствие invalid IDs и хотя бы одна citation. Он не
  является экспертной юридической или полной semantic-оценкой.
- Официальные сайты меняют разметку, перенаправления и доступность; некоторые
  страницы блокируют автоматическую загрузку, а PDF и динамические страницы
  могут извлекаться неполно.
- Поиск ограничен разрешёнными доменами и содержимым, которое удалось
  загрузить. Отсутствие evidence должно приводить к осторожному ответу или
  технической неудаче, а не к дополнению из памяти модели.
- Финальный safety-check намеренно узкий и детерминированный. Он блокирует
  известные практические паттерны мошенничества, но не заменяет модерацию,
  юриста или human review.
- Session IDs первичного benchmark были изолированы внутри одного run, но не
  глобально между разными reporting DB. Первичный phase-8 benchmark
  переиспользовал часть старой Agno history и не является release evidence.
- `citation_count` первичного отчёта вычислялся по Researcher report до
  Validator и Manager; наличие citations в финальном body отдельно не
  гарантировалось.
