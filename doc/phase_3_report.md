# Отчёт о выполнении этапа 3

## Программная проверка citations

**Дата проверки:** 26 июля 2026 года

**Статус:** завершён `[x]`

**Модель интеграционного запуска:** `qwen2.5:14b`

**Версия Agno:** `2.7.4`

## 1. Цель этапа

Цель этапа — проверять citations Researcher детерминированным Python-кодом,
не полагаясь на утверждения модели или добровольный вызов инструмента.

После изменения Workflow должен:

1. извлекать citations точного формата `[S<number>]`;
2. получать разрешённые source IDs из загруженного source package;
3. считать общее число citations и уникальные использованные IDs;
4. обнаруживать выдуманные source IDs;
5. сохранять результат отдельным шагом Workflow;
6. передавать результат Validator до исправления отчёта.

## 2. Выполненные изменения

### 2.1. Структура результата

В `visa_workflow.py` добавлена неизменяемая структура:

```python
@dataclass(frozen=True)
class CitationCheckResult:
    citation_count: int
    used_source_ids: tuple[str, ...]
    invalid_source_ids: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    has_citations: bool
```

Значения имеют следующую семантику:

- `citation_count` — количество всех вхождений, включая повторения;
- `used_source_ids` — уникальные IDs, найденные в отчёте;
- `invalid_source_ids` — использованные IDs, отсутствующие в source package;
- `allowed_source_ids` — нормализованные IDs загруженных источников;
- `has_citations` — найдено ли хотя бы одно корректно оформленное вхождение.

### 2.2. Детерминированное извлечение

Функция `check_report_citations` использует точное регулярное выражение:

```python
r"\[(S\d+)\]"
```

Поэтому распознаются `[S1]`, `[S2]`, `[S10]`, но не распознаются:

- `[s1]`;
- `[S 1]`;
- `[S-1]`;
- обычный текст `S1` без квадратных скобок.

Source IDs из package нормализуются в верхний регистр. Уникальные IDs
сортируются по числовой части, поэтому порядок выглядит как `S1`, `S2`,
`S10`, а не `S1`, `S10`, `S2`.

### 2.3. Отдельный шаг Workflow

Между Researcher и Validator добавлен шаг:

```text
analyze_official_sources
    ↓
check_research_citations
    ↓
validate_research
```

Шаг возвращает JSON:

```json
{
  "citation_count": 2,
  "used_source_ids": ["S1", "S99"],
  "invalid_source_ids": ["S99"],
  "allowed_source_ids": ["S1", "S2"],
  "has_citations": true
}
```

Результат сохраняется в `WorkflowRunOutput.step_results`. Это позволяет
последующим этапам записать citation-метрики в SQLite и benchmark XLSX без
повторного анализа текста.

Для шага установлены:

```python
max_retries=0
on_error="fail"
```

Ошибка программной проверки поэтому не пропускается и не запускает Validator
с отсутствующим результатом.

### 2.4. Передача результата Validator

Validator получает три раздела:

```text
SOURCE PACKAGE
PROGRAMMATIC CITATION CHECK
RESEARCH REPORT TO VALIDATE
```

Программный результат объявлен authoritative для синтаксиса и существования
source IDs. Validator получает инструкции:

- при `has_citations=false` удалить неподтверждённые факты и явно сообщить,
  что отчёт не содержит проверяемых citations;
- удалить недопустимые IDs и утверждения, не подтверждённые разрешёнными
  источниками;
- не придумывать заменяющие source IDs.

Программная проверка не зависит от LLM-инструмента `check_source_ids`.

## 3. Автоматические тесты

Команда проверки:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 30 tests

OK
```

Новые тесты находятся в `tests/test_citation_check.py`.

| Проверка | Результат |
|---|---|
| Корректные `[S1]` и `[S2]` | успешно |
| Повторяющиеся citations | подсчитаны все вхождения |
| Неизвестный `[S99]` | добавлен в `invalid_source_ids` |
| Текст без citations | `has_citations=false` |
| Смешанный регистр `[s1]` | не распознан |
| Некорректные `[S 1]` и `[S-1]` | не распознаны |
| Числовая сортировка IDs | успешно |
| Сериализация результата в JSON | успешно |
| Сохранение метрик в `StepOutput` | успешно |
| Передача результата в prompt Validator | успешно |
| Запуск Validator с citation-check | успешно |

Синтаксис проверен командой:

```bash
venv/bin/python -m py_compile \
    visa_workflow.py \
    tests/test_citation_check.py \
    tests/test_tool_call_control.py
```

Команда завершилась без ошибок.

## 4. Интеграционная проверка

### 4.1. Условия запуска

Ollama была доступна на `http://localhost:11434`. Использовалась модель
`qwen2.5:14b`.

Source package содержал один разрешённый источник `S1`. Тестовый отчёт
Researcher намеренно содержал правильный и выдуманный IDs:

```text
O-1A is an immigrant visa [S99].
It concerns sciences and business [S1].
```

Программная проверка сформировала:

```json
{
  "citation_count": 2,
  "used_source_ids": ["S1", "S99"],
  "invalid_source_ids": ["S99"],
  "allowed_source_ids": ["S1"],
  "has_citations": true
}
```

### 4.2. Результат Validator

Citation-check JSON был передан настоящему Validator. Модель вернула:

```text
O-1A concerns sciences and business [S1].
```

Проверка подтвердила:

```text
validator_nonempty = true
invalid_id_removed = true
valid_id_preserved = true
```

Выдуманный `[S99]` и связанная с ним неправильная классификация были удалены,
а подтверждённое утверждение с `[S1]` сохранилось.

## 5. Проверка критериев готовности

| Критерий | Статус | Подтверждение |
|---|---:|---|
| Citations извлекаются Python-кодом | выполнен | точное регулярное выражение |
| Разрешённые IDs берутся из source package | выполнен | нормализация package |
| Количество citations считается | выполнен | повторы учитываются |
| Уникальные использованные IDs сохраняются | выполнен | `used_source_ids` |
| Выдуманные IDs обнаруживаются | выполнен | тест `[S99]` |
| Отсутствие citations обнаруживается | выполнен | `has_citations=false` |
| Результат отражён в Workflow | выполнен | отдельный `StepOutput` |
| Результат передаётся Validator | выполнен | unit- и интеграционная проверка |
| Проверка не зависит от LLM tool | выполнен | чистая Python-функция |
| Все автоматические тесты проходят | выполнен | 30 тестов, `OK` |
| Реальный Validator удаляет invalid ID | выполнен | запуск `qwen2.5:14b` |

**Итог:** критерий готовности этапа 3 выполнен. Выдуманные source IDs
обнаруживаются детерминированно, сохраняются в результате Workflow и
передаются Validator.

## 6. Наблюдения и ограничения

1. Этап проверяет citations отчёта Researcher до Validator. Повторная
   программная проверка уже исправленного отчёта пока не выполняется.
2. Citation-метрики присутствуют в `step_results`, но ещё не записываются в
   benchmark SQLite и XLSX. Это относится к этапу 6.
3. Validator пока получает полный source package в prompt и не использует
   инструменты выборочного чтения. Это относится к этапу 4.
4. Решение об удалении или переформулировании неподтверждённого утверждения
   пока выполняет Validator. Детерминированная часть только обнаруживает
   отсутствие или недопустимость citations.

## 7. Изменённые файлы

- `visa_workflow.py` — `CitationCheckResult`, функции проверки и
  сериализации, новый шаг Workflow и prompt Validator;
- `tests/test_citation_check.py` — тесты этапа 3;
- `doc/WORKFLOW_REFACTOR_PLAN.md` — статус и результат проверки этапа;
- `doc/phase_3_report.md` — настоящий отчёт.
