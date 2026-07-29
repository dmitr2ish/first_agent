# Отчёт о выполнении этапа 6

## Расширение benchmark-отчётности

**Дата проверки:** 27 июля 2026 года

**Статус:** завершён `[x]`

**Модель production-запуска:** `qwen2.5:14b`

## 1. Цель этапа

Цель этапа — сделать SQLite воспроизводимым источником benchmark-данных:
сохранять агрегированные показатели interaction, отдельные tool calls и
полностью пересобирать из базы читаемый XLSX.

## 2. Передача telemetry из Workflow

Researcher и Validator уже формировали `ToolCallSummary`, но до этапа 6 эти
данные не выходили за пределы функции шага.

Теперь telemetry передаётся через штатный канал Agno:

```text
StepOutput.metrics
└── RunMetrics.additional_metrics
    └── workflow_telemetry
```

Контент шагов остаётся строкой прежнего формата, поэтому Researcher report,
citation JSON, Validator report и final answer продолжают передаваться между
шагами без изменений.

Для каждого tool call сохраняются:

- имя инструмента;
- аргументы;
- номер controlled attempt;
- success/error;
- duration;
- evidence-read флаг;
- result preview.

Preview ограничен 240 символами. Полный source content в таблицу tool calls
и лист `Tool Calls` не копируется.

## 3. SQLite-схема

### 3.1. Interaction metrics

В таблицу `interactions` добавлены:

```text
tool_call_count
tool_names
tool_error_count
evidence_read_performed
citation_count
used_source_ids
invalid_source_ids
source_count
researcher_success
validator_success
final_status
```

Списки имён и source IDs сохраняются как JSON. Boolean-значения сохраняются
как SQLite `INTEGER`.

### 3.2. Tool-call events

Добавлена таблица `tool_calls`:

```text
interaction_id
sequence_number
step_name
agent_name
attempt
tool_name
arguments_json
success
error
duration_seconds
evidence_read
result_preview
```

Запись interaction и всех его tool calls выполняется одной транзакцией.
Внешний ключ использует `ON DELETE CASCADE`.

## 4. Миграция

При инициализации reporter читает `PRAGMA table_info(interactions)` и
добавляет только отсутствующие phase-6 колонки. Таблица `tool_calls` и её
индекс создаются через `CREATE TABLE/INDEX IF NOT EXISTS`.

Проверены два сценария:

1. автоматически созданная копия phase-5 схемы с существующей строкой;
2. SQLite backup текущей `reports/experiment_results.db`, включающий WAL.

Во втором сценарии:

```text
existing interactions = 3
migrated interactions = 3
new columns present = true
tool_calls table present = true
```

Исходная пользовательская база при проверке не изменялась.

## 5. XLSX

`export_xlsx()` читает из SQLite:

- experiment runs;
- interactions;
- tool calls.

Workbook содержит четыре листа:

1. `Runs`;
2. `Interactions`;
3. `Step Outputs`;
4. `Tool Calls`.

В `Interactions` добавлены агрегированные phase-6 колонки. `Tool Calls`
содержит одну строку на фактический вызов. Детали tool calls не дублируются в
XLSX-колонке `Step Outputs / Metrics`; там остаётся компактная summary.

Для всех листов настроены:

- контрастная строка заголовков;
- freeze panes;
- auto-filter;
- перенос текста;
- подходящие ширины колонок;
- числовые форматы duration и count;
- скрытые gridlines.

Визуально проверены все четыре листа после рендера. Формульный scan не нашёл
`#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?` или `#N/A`.

## 6. Автоматические тесты

Команда:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 53 tests

OK
```

Новые тесты проверяют:

| Проверка | Результат |
|---|---:|
| Interaction с полным набором metrics | успешно |
| Несколько tool calls Researcher | успешно |
| Tool error | успешно |
| Researcher/Validator telemetry channel | успешно |
| Citation telemetry channel | успешно |
| Result preview не длиннее 240 символов | успешно |
| Failure final status | успешно |
| Миграция phase-5 схемы | успешно |
| Повторная сборка XLSX только из SQLite | успешно |
| Состав листов, заголовки и число строк | успешно |
| Header style, freeze panes и gridlines | успешно |

## 7. Production-интеграция

Вопрос:

```text
What type of US visa classification is O-1A?
```

Workflow завершил шаги:

```text
build_search_query
collect_official_sources
analyze_official_sources
check_research_citations
validate_research
create_final_answer
```

Сохранённые агрегаты:

```json
{
  "tool_call_count": 5,
  "tool_error_count": 0,
  "evidence_read_performed": true,
  "citation_count": 5,
  "used_source_ids": ["S1"],
  "invalid_source_ids": [],
  "source_count": 5,
  "researcher_success": true,
  "validator_success": true,
  "final_status": "COMPLETED"
}
```

Отдельные events содержат три tool calls Researcher и два tool calls
Validator. Временный XLSX успешно создан из той же SQLite.

## 8. Проверка критериев готовности

| Критерий | Статус |
|---|---:|
| SQLite остаётся источником истины | выполнен |
| Interaction metrics сохраняются | выполнен |
| Tool calls сохраняются отдельно | выполнен |
| Лист `Tool Calls` добавлен | выполнен |
| Старые XLSX-листы сохранены | выполнен |
| Старая SQLite-схема мигрируется | выполнен |
| Preview source content ограничен | выполнен |
| XLSX восстанавливается из SQLite | выполнен |
| Визуальная проверка пройдена | выполнен |
| Production Workflow отражён в SQLite/XLSX | выполнен |

**Итог:** один полный Workflow run воспроизводимо появляется в SQLite и XLSX
вместе с sources, citation metrics и отдельными tool calls. Критерий
готовности этапа 6 выполнен полностью.

## 9. Изменённые файлы

- `visa_workflow.py`;
- `experiment_reporter.py`;
- `tests/test_citation_check.py`;
- `tests/test_tool_call_control.py`;
- `tests/test_validator_control.py`;
- `tests/test_experiment_reporter.py`;
- `doc/WORKFLOW_REFACTOR_PLAN.md`;
- `doc/phase_6_report.md`;
- `doc/README.md`.
