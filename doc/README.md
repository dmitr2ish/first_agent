# Документация проекта

Документы по реализации и проверке Visa Research Workflow.

## Этап 1. Подключение tools к Researcher

- [Отчёт о выполнении этапа 1](phase_1_report.md) — цель, выполненные работы,
  тесты, интеграционный запуск, результаты и критерии готовности.
- [Реализация этапа 1](phase_1_implementation.md) — подробное учебное
  объяснение архитектуры, `dependencies`, инструментов Researcher,
  трассировки и способов самостоятельной проверки.

## Этап 2. Программный контроль tool calls

- [Отчёт о выполнении этапа 2](phase_2_report.md) — извлечение фактических
  tool calls, правило evidence read, контролируемый retry, тесты и
  интеграционная проверка.

## Этап 3. Программная проверка citations

- [Отчёт о выполнении этапа 3](phase_3_report.md) — детерминированное
  извлечение citations, обнаружение недопустимых source IDs, отдельный шаг
  Workflow и передача результата Validator.

## Этап 4. Подключение tools к Validator

- [Отчёт о выполнении этапа 4](phase_4_report.md) — source tools Validator,
  отдельный контроль tool calls, проверка evidence и запрет новых URL или
  source IDs.

Общий план дальнейшего рефакторинга находится в
[`WORKFLOW_REFACTOR_PLAN.md`](WORKFLOW_REFACTOR_PLAN.md).
