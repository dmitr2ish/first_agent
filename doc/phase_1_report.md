# Отчёт о выполнении этапа 1

## Подключение tools к Researcher

**Дата проверки:** 22 июля 2026 года

**Статус:** завершён `[x]`

**Модель интеграционного запуска:** `qwen2.5:14b`

**Версия Agno:** `2.7.4`

## 1. Цель этапа

Цель этапа — перевести Researcher с пассивного получения полного текста
источников в prompt на настоящую работу с источниками через Agno tool calls.

После изменения Researcher должен:

1. получить список официальных источников;
2. найти релевантные фрагменты внутри загруженных документов;
3. прочитать нужные части документов;
4. сформировать непустой исследовательский отчёт;
5. сослаться на источники идентификаторами вида `[S1]`.

Вызов только `list_official_sources` не считается чтением evidence. Для
выполнения критерия нужен как минимум один успешный вызов
`search_official_sources` или `read_official_source`.

## 2. Выполненные изменения

### 2.1. Инструменты Researcher

В `visa_workflow.py` импортированы и подключены к Researcher:

- `list_official_sources`;
- `search_official_sources`;
- `read_official_source`;
- `trace_tool_call` в качестве tool hook.

Конфигурация Researcher содержит:

```python
tools=[
    list_official_sources,
    search_official_sources,
    read_official_source,
],
tool_hooks=[trace_tool_call],
tool_call_limit=8,
```

Лимит в восемь вызовов защищает Workflow от слишком длинной или циклической
последовательности tool calls.

### 2.2. Передача source package

Пакет официальных источников больше не вставляется полностью в prompt
Researcher. Он передаётся через run-level `dependencies`:

```python
researcher.run(
    prompt,
    stream=False,
    dependencies={
        "source_package": source_package,
    },
)
```

Инструменты получают этот пакет через `RunContext.dependencies`. Благодаря
этому полный текст становится доступен модели только после настоящего вызова
инструмента поиска или чтения.

### 2.3. Инструкции Researcher

Системные инструкции обновлены. Researcher обязан:

1. вызвать `list_official_sources`;
2. затем вызвать `search_official_sources` и/или `read_official_source`;
3. использовать только факты из результатов tools;
4. добавлять `[S1]`, `[S2]` и другие source IDs к фактическим утверждениям;
5. не придумывать source IDs и URL.

### 2.4. Трассировка tool calls

`trace_tool_call` выводит в stdout:

- идентификатор запуска агента;
- имя инструмента;
- переданные аргументы;
- результат или сообщение об ошибке;
- длительность выполнения.

Длинные аргументы и результаты сокращаются только при печати. Сам инструмент
и модель получают настоящий результат без изменения.

## 3. Автоматические тесты

Команда проверки:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 7 tests in 0.001s

OK
```

Проверены следующие случаи:

| Проверка | Результат |
|---|---|
| Передача `source_package` через `dependencies` | успешно |
| Отсутствие полного source content в prompt | успешно |
| Получение списка доступных источников | успешно |
| Поиск по содержимому источников | успешно |
| Чтение выбранного источника | успешно |
| Обработка неизвестного `source_id` | успешно |
| Ограничения `start_char`, `max_chars`, `max_results` | успешно |
| Вывод имени, аргументов, результата и времени tool call | успешно |

Синтаксис модулей дополнительно проверен командой:

```bash
venv/bin/python -m py_compile source_analysis_tools.py visa_workflow.py
```

Команда завершилась без ошибок.

## 4. Интеграционная проверка

### 4.1. Условия запуска

Ollama была доступна на `http://localhost:11434`. Для запуска выбрана модель
`qwen2.5:14b`, у которой Ollama сообщает capability `tools`.

Тестовый вопрос:

```text
Какие официальные критерии USCIS установлены для визы O-1A?
```

### 4.2. Выполнение Workflow

Query Builder сформировал запрос:

```text
official USCIS criteria for O-1A visa requirements
```

Source Collector загрузил пять официальных источников USCIS и присвоил им
идентификаторы `S1`–`S5`.

Researcher выполнил три настоящих tool calls:

```text
list_official_sources()
search_official_sources(
    query="O-1A visa criteria",
    max_results=5,
)
read_official_source(
    source_id="S2",
    start_char=0,
    max_chars=1000,
)
```

Для каждого вызова hook напечатал аргументы, результат и длительность. Оба
инструмента чтения evidence — `search_official_sources` и
`read_official_source` — завершились успешно.

Researcher сформировал непустой отчёт с citations `[S2]` и `[S5]`. После него
Validator и Manager успешно завершили свои шаги. Полный Python-процесс
завершился с кодом `0`.

## 5. Проверка критериев готовности

| Критерий | Статус | Подтверждение |
|---|---:|---|
| Tools переданы Researcher | выполнен | Agno зарегистрировал три функции |
| Source package передаётся через dependencies | выполнен | unit-тест и debug output |
| Полный пакет удалён из prompt Researcher | выполнен | unit-тест |
| Есть успешное чтение evidence | выполнен | успешные `search` и `read` |
| Tool calls видны в stdout | выполнен | hook вывел имя, аргументы, результат и время |
| Researcher возвращает отчёт | выполнен | непустой отчёт с `[S2]`, `[S5]` |
| Все тесты этапа проходят | выполнен | 7 тестов, `OK` |

**Итог:** критерий готовности этапа 1 выполнен полностью.

## 6. Наблюдения и ограничения

Во время проверки обнаружены два обстоятельства, не блокирующие этап 1:

1. Некоторые страницы `travel.state.gov` вернули HTTP `403`, однако Source
   Collector успешно загрузил пять подходящих страниц USCIS.
2. Validator обрабатывал полный source package около 205 секунд. Подключение
   tools к Validator и уменьшение его prompt относятся к этапу 4.

Также инструкции пока не являются программной гарантией вызова tools.
Детерминированное извлечение и проверка tool-call данных, признак
`evidence_read_performed` и контролируемый retry относятся к этапу 2.

## 7. Изменённые файлы

- `visa_workflow.py` — конфигурация Researcher, новый prompt и передача
  dependencies;
- `source_analysis_tools.py` — инструменты анализа и трассировка;
- `tests/test_source_analysis_tools.py` — тесты source-analysis tools;
- `tests/test_researcher_dependencies.py` — тест передачи source package;
- `WORKFLOW_REFACTOR_PLAN.md` — статус и результат проверки этапа.
