# Changelog

Формат основан на Keep a Changelog. До первого подтверждённого release все
изменения находятся в разделе `Unreleased`.

## Unreleased

### Added

- Каноническая документация проекта и корневой README.
- Зафиксированные runtime/development dependencies.
- Offline CI для locked dependency install, `pip check`, compile, import
  smoke и unit tests.
- Release-hardening checklist после сквозного ревью фаз 0–8.
- Изоляция CLI- и benchmark-сессий между experiment runs.
- Детерминированные проверки Validator и финального ответа.
- Локализованный legal disclaimer, гарантируемый Python после Manager.
- Связь citations с фактически прочитанными source IDs.
- Разделение technical, semantic и release-оценки benchmark.
- Fail-closed release gate для отсутствующих semantic/safety результатов.
- Semantic expectations для всех benchmark turns.
- Atomic record/replay frozen evidence для честного A/B моделей.
- Финальный evidence-manifest SHA-256 сохраняется в provenance самого run.
- Ollama/model/tool preflight и runtime provenance.
- Debug/trace opt-in, HTTPS-only retrieval и XLSX formula sanitization.
- Внешняя Agno telemetry явно выключена для всех Agents и Workflow.
- Dirty-tree digest в git revision metadata.

### In progress

- Повторный multi-model benchmark на чистом commit и чистых history/reporting
  DB.
- Финальный SQLite/XLSX/semantic review release artifacts.

## Phase 8 snapshot — 2026-07-29

- Добавлены final fraud guard, `Benchmark Summary` и
  `final_safety_blocked`.
- Выполнен первичный multi-model benchmark из 11 turns.
- Итоговый аудит обнаружил загрязнение Qwen-прогона старой Agno-историей;
  результаты сохранены как диагностический snapshot, но не являются
  release evidence.

## Phase 7 — 2026-07-29

- Добавлен versioned benchmark из 9 сценариев и 11 turns.
- Реализована изоляция сценариев внутри одного benchmark run.
- Добавлены technical pass/fail и metadata воспроизводимости.

## Phase 6 — 2026-07-29

- Расширена SQLite/XLSX-отчётность и добавлена таблица tool calls.

## Phase 5 — 2026-07-27

- Workflow, CLI, benchmark и общая конфигурация разделены по модулям.

## Phases 1–4 — 2026-07-22…2026-07-27

- Researcher и Validator переведены на Agno tools.
- Добавлены evidence-read control, controlled retry и citation/source-ID
  проверки.

Подробная история с критериями каждой фазы находится в
[`doc/PROJECT_DOCUMENTATION.md`](doc/PROJECT_DOCUMENTATION.md).
