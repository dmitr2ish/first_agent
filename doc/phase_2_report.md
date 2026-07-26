# Отчёт о выполнении этапа 2

## Программный контроль tool calls

**Дата проверки:** 22 июля 2026 года

**Повторное ревью и проверка остановки Workflow:** 26 июля 2026 года

**Статус:** завершён `[x]`

**Модель интеграционного запуска:** `qwen2.5:14b`

**Версия Agno:** `2.7.4`

**Commit реализации:** `45a2ef3`

## 1. Цель этапа

Цель этапа — сделать вызовы инструментов Researcher программно проверяемым
условием успешного выполнения Workflow.

После этапа 1 модель уже могла вызывать инструменты, но Workflow проверял
только наличие непустого текста в ответе. Поэтому Researcher мог написать,
что изучил источники, не выполнив настоящего вызова чтения.

После изменения Workflow должен:

1. извлекать сведения о выполненных инструментах из ответа Agno;
2. отличать просмотр списка источников от чтения evidence;
3. считать успешные вызовы, ошибки и длительность;
4. выполнить не более одного retry, если evidence не читался;
5. завершить шаг Researcher с ошибкой, если retry также не дал evidence.

## 2. Выполненные изменения

### 2.1. Источник данных о tool calls

Источником истины служит поле `RunOutput.tools`, которое Agno заполняет
объектами `ToolExecution`.

Для каждого фактического вызова доступны:

- `tool_name` — имя инструмента;
- `tool_args` — переданные аргументы;
- `tool_call_error` — признак ошибки выполнения;
- `result` — результат инструмента;
- `metrics.duration` — длительность, если она доступна.

Stdout-трассировка этапа 1 продолжает использоваться для наблюдения, но
решение об успехе больше не зависит от напечатанного текста.

### 2.2. Нормализованные структуры

В `visa_workflow.py` добавлена структура одного вызова:

```python
@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    success: bool
    error: bool
    duration_seconds: float | None
    evidence_read: bool
```

И агрегированная структура запуска:

```python
@dataclass(frozen=True)
class ToolCallSummary:
    calls: tuple[ToolCallRecord, ...]
    total_count: int
    tool_names: tuple[str, ...]
    successful_count: int
    error_count: int
    evidence_read_performed: bool
    total_duration_seconds: float | None
```

Отдельные записи сохраняются внутри summary, чтобы на следующих этапах их
можно было передать в benchmark и записать в SQLite без повторного анализа
ответа Agno.

### 2.3. Правило успешного чтения evidence

Вызов считается чтением evidence только при выполнении всех условий:

1. имя инструмента — `search_official_sources` или
   `read_official_source`;
2. Agno не установил `tool_call_error=True`;
3. результат не содержит прикладную JSON-ошибку в поле `error`;
4. поиск вернул хотя бы один элемент в `matches` либо чтение вернуло
   непустое поле `content`.

Следовательно:

- `list_official_sources` не считается чтением;
- поиск без совпадений не считается чтением;
- чтение неизвестного `source_id` не считается чтением;
- пустой результат чтения не считается evidence;
- фраза модели о выполненном исследовании не влияет на проверку.

### 2.4. Контролируемый retry

Функция `run_researcher_with_evidence_control` выполняет Researcher один раз и
строит `ToolCallSummary`.

Если evidence не читался, запускается ровно одна повторная попытка с
дополнительной инструкцией:

```text
MANDATORY RETRY:
Your previous response did not successfully read evidence. You must call
search_official_sources or read_official_source and receive relevant source
content before writing the report.
```

Вызовы обеих попыток объединяются в один summary. Третья попытка не
выполняется.

### 2.5. Результат шага Workflow

Функция `build_researcher_step_output` применяет итоговую программную
проверку:

```text
evidence_read_performed = true
    и отчёт непустой
        → StepOutput(success=True)

evidence_read_performed = false
    после двух попыток
        → EvidenceReadRequiredError
        → Workflow останавливается
```

Таким образом, непустой ответ модели больше не является достаточным условием
успеха. Для `research_step` установлены `max_retries=0` и
`on_error="fail"`. Это не позволяет Agno повторить всю двухпопыточную
последовательность или пропустить ошибку и перейти к Validator.

## 3. Автоматические тесты

Команда проверки:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 21 tests

OK
```

Новые тесты этапа 2 находятся в `tests/test_tool_call_control.py`.

| Проверка | Результат |
|---|---|
| Ответ без tool calls | успешно |
| Только `list_official_sources` | evidence не засчитано |
| Успешный поиск с совпадениями | evidence засчитано |
| Успешное чтение с непустым content | evidence засчитано |
| `tool_call_error=True` | ошибка подсчитана |
| JSON-результат с полем `error` | ошибка подсчитана |
| Поиск с пустым `matches` | evidence не засчитано |
| Извлечение длительности | успешно |
| Evidence в первой попытке | retry не выполняется |
| Отсутствие evidence | выполняется один retry |
| Evidence во второй попытке | Workflow продолжает работу |
| Отсутствие evidence после retry | возникает `EvidenceReadRequiredError` |
| Пустой отчёт после чтения evidence | возникает `ResearcherReportEmptyError` |
| Передача source package при retry | dependencies сохранены |
| Ошибка evidence внутри Agno Workflow | downstream-шаг не запускается |
| Успешное evidence внутри Agno Workflow | downstream-шаг запускается |

Синтаксис дополнительно проверен командой:

```bash
venv/bin/python -m py_compile \
    visa_workflow.py \
    source_analysis_tools.py \
    tests/test_tool_call_control.py
```

Команда завершилась без ошибок.

## 4. Интеграционная проверка

### 4.1. Условия запуска

Ollama была доступна на `http://localhost:11434`. Использовалась модель
`qwen2.5:14b` с capability `tools`.

Чтобы проверить именно границу Researcher и Agno tool calling без внешней
нестабильности веб-загрузки, запуск использовал заранее подготовленный
`source_package` с официальным URL USCIS и тестовым фрагментом содержания.

Тестовое задание:

```text
Using the available official source, state what fields O-1A covers.
Cite [S1].
```

### 4.2. Реальные tool calls

Researcher выполнил:

```text
list_official_sources()
search_official_sources(
    query="fields covered by O-1A",
    max_results=3,
)
read_official_source(
    source_id="S1",
    start_char=0,
    max_chars=5000,
)
```

Agno вернул все три выполнения в `RunOutput.tools`. Программный summary
зафиксировал:

```text
attempt_count = 1
tool_names = (
    "list_official_sources",
    "search_official_sources",
    "read_official_source",
)
total_count = 3
error_count = 0
evidence_read_performed = true
report_nonempty = true
```

Retry не потребовался.

Негативный сценарий проверен настоящим Agno Workflow с последовательными
ответами Researcher: после первой попытки без evidence выполняется один retry,
а после второй неудачной попытки возникает `EvidenceReadRequiredError`.
Установленная политика `on_error="fail"` останавливает Workflow, поэтому
Validator и Manager не запускаются. Тест также подтверждает, что Researcher
был вызван ровно два раза.

Отдельный позитивный тест настоящего Workflow подтверждает, что успешное
чтение evidence позволяет выполнить следующий шаг и завершить Workflow.

## 5. Проверка критериев готовности

| Критерий | Статус | Подтверждение |
|---|---:|---|
| Tool calls извлекаются из ответа Agno | выполнен | используется `RunOutput.tools` |
| Общее число и имена вызовов считаются | выполнен | `ToolCallSummary` |
| Успехи и ошибки считаются | выполнен | тесты технических и прикладных ошибок |
| Длительность извлекается | выполнен | используется `metrics.duration` |
| `list` не считается чтением | выполнен | отдельный unit-тест |
| Пустой поиск не считается чтением | выполнен | отдельный unit-тест |
| Есть не более одного retry | выполнен | проверено числом вызовов Researcher |
| Workflow завершается ошибкой без evidence | выполнен | `EvidenceReadRequiredError`, downstream не запущен |
| Внешний retry шага отключён | выполнен | `max_retries=0` |
| Ошибка шага не пропускается | выполнен | `on_error="fail"` |
| Успешное чтение позволяет продолжить Workflow | выполнен | интеграционный тест Workflow |
| Реальная модель создаёт проверяемые tool calls | выполнен | запуск `qwen2.5:14b` |
| Все автоматические тесты проходят | выполнен | 21 тест, `OK` |

**Итог:** критерий готовности этапа 2 выполнен полностью. Workflow
программно отличает утверждение модели об исследовании от фактического чтения
источников.

## 6. Наблюдения и ограничения

1. Tool-call summary пока используется внутри шага Researcher, но ещё не
   сохраняется в benchmark SQLite и XLSX. Это относится к этапу 6.
2. Программная проверка подтверждает чтение evidence, но пока не проверяет
   корректность citation `[S1]`, `[S2]` в тексте. Это относится к этапу 3.
3. Validator пока получает полный source package в prompt и не использует
   source-analysis tools. Это относится к этапу 4.
4. Реальная модель проверена на уровне Researcher с подготовленным source
   package. Остановка и продолжение Agno Workflow проверены
   детерминированными интеграционными тестами без обращения к модели.

## 7. Изменённые файлы

- `visa_workflow.py` — структуры tool-call данных, извлечение метрик,
  контролируемый retry и программное завершение шага;
- `tests/test_tool_call_control.py` — unit- и интеграционные тесты этапа 2;
- `doc/WORKFLOW_REFACTOR_PLAN.md` — статус и результат проверки этапа.
