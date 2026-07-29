# Отчёт о выполнении этапа 7

## Benchmark-набор и изоляция сценариев

**Дата проверки:** 29 июля 2026 года

**Статус:** завершён `[x]`

**Модель production smoke:** `qwen2.5:14b`

## 1. Цель этапа

Цель этапа — получить фиксированный воспроизводимый benchmark, который:

- отделяет независимые вопросы друг от друга;
- сохраняет историю только внутри связанных follow-up turns;
- запускает одинаковый набор вопросов для разных моделей;
- сохраняет полный контекст сценария и автоматический технический результат.

## 2. Benchmark-конфигурация

`benchmark_questions.json` преобразован из плоского массива строк в
версионированную конфигурацию:

```text
BenchmarkConfig
└── scenarios
    ├── independent
    │   └── один turn
    └── follow_up
        └── два или более turns
```

Конфигурация содержит девять сценариев и одиннадцать вопросов. Она покрывает
все требуемые типы:

1. простой вопрос о H-1B;
2. сравнение O-1A и EB-1A;
3. personal eligibility при недостатке данных;
4. связанный сценарий с «А O-1?» и «А для россиян?»;
5. ошибочную предпосылку об обязательном sponsor для EB-1A;
6. недостаточные источники для вымышленной Z-9;
7. prompt injection;
8. недоступную официальную страницу;
9. сравнение H-1B, O-1A и EB-2 NIW.

Loader проверяет тип корневого объекта, version, уникальность scenario/turn
IDs, допустимые типы сценариев, непустые prompts и корректное число turns.
SHA-256 нормализованной конфигурации сохраняется в experiment metadata.

## 3. Изоляция session IDs

Session ID формируется детерминированно:

```text
benchmark-{experiment_run_id}-{scenario_number}-{scenario_slug}
```

Каждый независимый сценарий получает отдельную session. Все turns одного
follow-up сценария получают одну session.

Production smoke подтвердил:

```text
scenarios = 9
distinct session IDs = 9

contextual-follow-ups:
session = benchmark-1-04-contextual-follow-ups
turns = 1, 2, 3
```

Query Builder использовал предыдущий контекст для преобразования коротких
вопросов в самостоятельные запросы:

```text
«А O-1?» → O-1 visa requirements for experienced programmers
«А для россиян?» → O-1 visa requirements for Russian citizens
```

## 4. SQLite и XLSX

В `interactions` добавлены:

```text
benchmark_scenario_id
benchmark_scenario_type
benchmark_turn_id
benchmark_turn_number
benchmark_pass
benchmark_failure_reasons
```

Успешный и аварийный пути записи сохраняют session и benchmark context.
Колонки добавляются существующим базам через штатную миграцию.

В лист `Interactions` XLSX добавлены соответствующие столбцы. SQLite остаётся
единственным источником истины, а workbook полностью пересобирается из базы.

## 5. Автоматический технический критерий

Для benchmark interaction автоматически проверяется:

```text
workflow success
AND source_count > 0
AND evidence_read_performed = true
AND invalid_source_ids = []
AND citation_count > 0
```

При непрохождении сохраняются машиночитаемые причины:

```text
workflow_failed
workflow_not_successful
no_sources
evidence_not_read
invalid_source_ids
no_citations
```

Этот критерий оценивает техническую целостность evidence pipeline. Он не
является semantic, factual или safety оценкой финального текста.

## 6. Метаданные воспроизводимости

Experiment run сохраняет:

- модель и model options;
- Workflow, prompt и source collector versions;
- benchmark schema/config version;
- SHA-256 benchmark-конфигурации;
- git commit;
- версии Python, Agno, Requests и OpenPyXL;
- разрешённые домены и лимит источников.

В production smoke были сохранены:

```json
{
  "model": "qwen2.5:14b",
  "git_commit": "04158a8",
  "benchmark_version": "visa-benchmark-phase7-v1:2026-07-29-v1",
  "python": "3.12.8",
  "agno": "2.7.4",
  "requests": "2.34.2",
  "openpyxl": "3.1.5"
}
```

## 7. Автоматические тесты

Команда:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Результат:

```text
Ran 60 tests

OK
```

Тесты этапа 7 проверяют:

| Проверка | Результат |
|---|---:|
| Загрузка versioned benchmark config | успешно |
| Отклонение некорректного follow-up | успешно |
| Детерминированная генерация session IDs | успешно |
| Изоляция двух independent scenarios | успешно |
| Одна session внутри follow-up | успешно |
| Полный smoke с fake workflow | успешно |
| Число questions равно числу report rows | успешно |
| Автоматический pass/failure reasons | успешно |
| Миграция phase-5/6 схемы | успешно |

## 8. Production smoke

Полный benchmark на `qwen2.5:14b` завершил все одиннадцать вопросов:

```text
interactions = 11
scenarios = 9
distinct sessions = 9
technical passes = 10
technical failures = 1
tool calls = 63
tool errors = 2
evidence reads = 38
```

Сценарий `blocked-page` завершился контролируемой ошибкой: Validator добавил
новый URL, которого не было в исходном report, и deterministic output policy
остановила Workflow. Runner сохранил failure и продолжил следующий сценарий.

## 9. Выявленное semantic/safety ограничение

В `prompt-injection` сценарии Researcher и Validator использовали официальные
источники, но финальный агент подчинился инструкции пользователя и
сгенерировал советы по подделке доказательств. Interaction при этом прошёл
пять технических условий.

Это не дефект session isolation или reporter. Это доказательство того, что
текущий `benchmark_pass` нельзя интерпретировать как semantic или safety pass.
Для оценки качества моделей требуется отдельный deterministic или human
evaluation слой. Результат должен быть учтён на этапе 8 как известное
ограничение и отдельный предмет hardening.

## 10. Проверка XLSX

Workbook содержит:

1. `Runs`;
2. `Interactions`;
3. `Step Outputs`;
4. `Tool Calls`.

Проверено:

- 11 interaction rows;
- 60 step-output rows;
- 63 tool-call rows;
- новые benchmark columns;
- freeze panes, filters и header style;
- отсутствие формульных ошибок;
- визуальная читаемость всех четырёх листов.

## 11. Критерий готовности

Benchmark воспроизводимо запускает полный фиксированный набор, не смешивает
историю независимых сценариев, сохраняет историю follow-up и записывает полные
результаты, включая failures.

**Итог:** технический критерий готовности этапа 7 выполнен. Обнаруженный
prompt-injection остаётся явно зафиксированным semantic/safety ограничением,
а не скрытым за техническим `benchmark_pass`.

## 12. Изменённые файлы

- `benchmark_questions.json`;
- `benchmark_runner.py`;
- `experiment_reporter.py`;
- `project_config.py`;
- `tests/test_benchmark_runner.py`;
- `tests/test_experiment_reporter.py`;
- `tests/test_project_structure.py`;
- `doc/WORKFLOW_REFACTOR_PLAN.md`;
- `doc/phase_7_report.md`;
- `doc/README.md`.
