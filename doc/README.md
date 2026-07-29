# Документация Visa Research Workflow

Главный документ проекта:

## [Полная документация проекта](PROJECT_DOCUMENTATION.md)

В одном каноническом документе собраны:

- overview, цели, область применения и ограничения;
- быстрый старт с Python и Ollama;
- структура репозитория, data flow, дизайн и архитектура;
- роли агентов, prompts, tools и controlled retries;
- evidence, citation, grounding и safety contracts;
- SQLite schema и пять листов XLSX;
- интерактивный CLI и versioned benchmark;
- sessions, версии и требования воспроизводимости;
- история фаз 0–8 и статус release hardening;
- тестирование, privacy/security threat model;
- known limitations, troubleshooting и release checklist.

Текущий план работ и критерии завершения phase 8:
[WORKFLOW_REFACTOR_PLAN.md](WORKFLOW_REFACTOR_PLAN.md).

## Исторические отчёты фаз

Эти документы являются подробными appendices. Они фиксируют реализацию и
результаты на момент каждой фазы и не заменяют актуальный статус в
канонической документации.

| Фаза | Тема | Документы |
|---|---|---|
| 1 | Tools Researcher | [отчёт](phase_1_report.md), [учебное объяснение](phase_1_implementation.md) |
| 2 | Программный контроль tool calls | [отчёт](phase_2_report.md) |
| 3 | Программная проверка citations | [отчёт](phase_3_report.md) |
| 4 | Tools и evidence control Validator | [отчёт](phase_4_report.md) |
| 5 | Разделение Workflow, CLI и benchmark | [отчёт](phase_5_report.md) |
| 6 | SQLite/XLSX telemetry | [отчёт](phase_6_report.md) |
| 7 | Versioned scenarios и session isolation | [отчёт](phase_7_report.md) |
| 8 | Release hardening; clean rerun pending | [отчёт](phase_8_report.md) |

Дополнительные материалы:

- [архитектурный snapshot phase 8](ARCHITECTURE.md);
- [изображение реального tool trace](assets/phase_8_tool_trace.png);
- [корневой README](../README.md);
- [история изменений](../CHANGELOG.md).

> После итогового сквозного ревью phase 8 переоткрыта. Session isolation,
> final grounding/safety policies и reporting hardening уже реализованы.
> Первичный multi-model benchmark остаётся диагностическим материалом и не
> считается release evidence до повторного запуска на чистом commit и новых
> изолированных БД.
