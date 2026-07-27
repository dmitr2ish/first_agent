# Отчёт о выполнении этапа 4

## Подключение tools к Validator

**Дата проверки:** 26 июля 2026 года

**Статус:** завершён `[x]`

**Модель интеграционного запуска:** `qwen2.5:14b`

**Версия Agno:** `2.7.4`

## 1. Цель этапа

Цель этапа — перевести Validator с пассивной обработки полного текста
источников на проверку ключевых утверждений через настоящие Agno tool calls.

Validator должен:

1. проверить использованные source IDs;
2. прочитать или найти evidence в загруженных официальных источниках;
3. исправить неверную классификацию;
4. удалить неподтверждённые утверждения и выдуманные IDs;
5. не добавлять новые факты, URL или source IDs.

## 2. Выполненные изменения

### 2.1. Инструменты Validator

Validator получил:

```python
tools=[
    check_source_ids,
    read_official_source,
    search_official_sources,
],
tool_hooks=[trace_tool_call],
tool_call_limit=6,
```

Source package передаётся через run-level `dependencies`, как у Researcher.
Полный package не вставляется в prompt. Validator получает content только
после настоящего вызова `read_official_source` или
`search_official_sources`.

### 2.2. Инструкции

Validator обязан использовать программный citation-check как authoritative,
вызвать `check_source_ids`, а затем прочитать или найти релевантное evidence.

Неподтверждённые предложения должны удаляться целиком. Запрещено сохранять их
с пометками `unsupported`, `unverified` или `invalid source`. Validator
отбрасывает исходные формулировки и заново собирает короткий исправленный
отчёт только из фактов, полученных через tools. Он возвращает результат без
описания процесса проверки.

### 2.3. Отдельный контроль tool calls

Добавлена структура `ValidatorRunResult`, содержащая:

- ответ Validator;
- отдельный `ToolCallSummary`;
- число попыток.

Вызов только `check_source_ids` не считается чтением evidence. Нужен успешный
`read_official_source` или `search_official_sources` с найденным фрагментом.

Если первая попытка не прочитала evidence, выполняется один retry. Для
Workflow Step установлены:

```python
max_retries=0
on_error="fail"
```

Это сохраняет максимум две модельные попытки и не позволяет Manager получить
непроверенный отчёт.

### 2.4. Постпроверка результата

`ValidatorOutputCheck` сравнивает исходный и исправленный отчёты.

Проверяются:

- неизвестные source IDs;
- новые source IDs, даже если они существуют в package;
- новые URL.

Source IDs ищутся как токены `S<number>` с квадратными скобками или без них.
URL нормализуются перед сравнением. При нарушении создаётся
`ValidatorOutputPolicyError`, и Workflow останавливается.

## 3. Автоматические тесты

Команда:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 42 tests

OK
```

Проверены:

| Проверка | Результат |
|---|---|
| `check_source_ids` с valid и invalid IDs | успешно |
| Source package передаётся через dependencies | успешно |
| Tool calls Validator считаются отдельно | успешно |
| Только `check_source_ids` | вызывает retry |
| Успешный `read` или `search` | evidence засчитано |
| Две попытки без evidence | фатальная ошибка |
| Исправление неверной классификации | успешно |
| Удаление неподтверждённого утверждения | успешно |
| Отчёт без citations возвращает нейтральный fallback | успешно |
| Недопустимый `[S99]` | результат отклонён |
| Недопустимый текстовый `S99` | результат отклонён |
| Новый разрешённый `[S2]` | результат отклонён |
| Новый URL | результат отклонён |

## 4. Интеграционная проверка

Тестовый source package содержал официальный фрагмент:

```text
USCIS describes O-1A as a nonimmigrant classification for individuals
with extraordinary ability in sciences, education, business, or athletics.
The excerpt does not guarantee approval for any applicant.
```

Подготовленный ошибочный отчёт:

```text
O-1A is an immigrant visa [S1].
Every programmer is guaranteed approval [S1].
An invented rule applies [S99].
```

В финальном интеграционном запуске Validator выполнил:

```text
read_official_source(source_id="S1", ...)
```

`check_source_ids` был зарегистрирован у Validator и отдельно проверен
автоматическим тестом с valid и invalid IDs.

Результат:

```text
O-1A is a nonimmigrant classification for individuals with extraordinary
ability in sciences, education, business, or athletics [S1].
```

Итоговые признаки:

```text
attempt_count = 2
tool_error_count = 0
evidence_read_performed = true
invalid_id_removed = true
classification_fixed = true
unsupported_claim_removed = true
```

## 5. Проверка критериев готовности

| Критерий | Статус |
|---|---:|
| Validator использует настоящие tools | выполнен |
| Source package передаётся через dependencies | выполнен |
| Citation-check передаётся Validator | выполнен |
| Tool calls ограничены | выполнен |
| Метрики Validator отделены от Researcher | выполнен |
| Evidence read проверяется программно | выполнен |
| Неверная классификация исправляется | выполнен |
| Выдуманный ID удаляется | выполнен |
| Неподтверждённое утверждение удаляется | выполнен |
| Новые IDs и URL запрещены | выполнен |
| Все тесты проходят | выполнен |

**Итог:** критерий готовности этапа 4 выполнен полностью.

## 6. Ограничения

1. Отдельный Validator tool summary пока не сохраняется в SQLite/XLSX. Это
   относится к этапу 6.
2. Проверка отсутствия новых фактов обеспечивается инструментами, prompt и
   интеграционными сценариями. Полностью определить семантическую новизну
   факта регулярными выражениями невозможно.

## 7. Изменённые файлы

- `visa_workflow.py`;
- `tests/test_source_analysis_tools.py`;
- `tests/test_citation_check.py`;
- `tests/test_validator_control.py`;
- `doc/WORKFLOW_REFACTOR_PLAN.md`;
- `doc/phase_4_report.md`.
