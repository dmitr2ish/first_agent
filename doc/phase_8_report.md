# Отчёт по этапу 8: финальная проверка и release rework

**Дата первичного запуска:** 29 июля 2026 года  
**Дата итогового review:** 29 июля 2026 года  
**Статус:** `[~]` hardening реализован; clean production rerun pending

> Первичный multi-model benchmark остаётся полезным диагностическим
> snapshot, но не является release evidence. Сквозной review обнаружил
> переиспользование старой Agno history и расхождение между техническим pass
> промежуточного Researcher report и качеством финального ответа.

## Цель

Доказать готовность проекта не только unit-тестами, но и воспроизводимым
production benchmark: одинаковые сценарии на выбранных локальных моделях,
проверяемые SQLite/XLSX-артефакты, фактические tool traces, проверенный
финальный body и актуальное описание архитектуры.

Первичная реализация доказала работу инфраструктуры, но не выполнила все
усиленные критерии после итогового review. Этап переоткрыт, чтобы исправить
контракты sessions, grounding, safety и release reproducibility.

## Реализованный snapshot

### Защита финального ответа

Этап 7 выявил, что технически успешный interaction может оставаться
семантически небезопасным: локальная модель выполнила prompt injection с
советами по фальсификации материалов. В первичной реализации этапа 8:

- вопрос и история явно обозначены для Visa Manager как недоверенные данные;
- system prompt запрещает подделку, искажение фактов и иммиграционное
  мошенничество;
- Python post-check ищет известные русские и английские fraud-паттерны;
- найденный небезопасный текст заменяется детерминированным отказом;
- срабатывание сохраняется как `final_safety_blocked` в SQLite и XLSX.

Этот regex-check является defense in depth и не заменяет общую moderation
policy. Кроме того, debug Agno мог показать raw Manager output до применения
post-check. Поэтому защита требует доработки до release.

### Демонстрационная benchmark-таблица

XLSX по-прежнему полностью пересобирается из SQLite. Первым листом добавлен
`Benchmark Summary` со сравнением runs:

- модель, версия и SHA-256 digest benchmark-конфига, git revision;
- число вопросов, technical passes/failures и pass rate;
- workflow failures, tool calls/errors, evidence reads и citations;
- invalid source IDs, safety blocks и средняя длительность.

Детальные листы `Runs`, `Interactions`, `Step Outputs` и `Tool Calls`
сохранены. В `Interactions` добавлена колонка `Safety Blocked`.

### Воспроизводимость

Версия prompts повышена до `visa-prompts-2026-07-29-v2`, версия схемы
benchmark — до `visa-benchmark-phase8-v1`. Git metadata использует
`git describe --always --dirty`.

Первичный запуск сравнивал:

- один файл `benchmark_questions.json`;
- совпадающий SHA-256 digest;
- одинаковые версии workflow, prompts и source collector;
- одинаковые `MODEL_OPTIONS`;
- одинаковый текст git revision и runtime component versions.

Итоговый review выявил, что этих условий недостаточно:

1. значение вроде `67eaf9f-dirty` не позволяет восстановить точный diff;
2. одинаковый локальный `experiment_run_id` в разных reporting DB мог
   породить тот же session ID в общей Agno history DB.

Release benchmark разрешён только на чистом commit, с уникальным invocation
namespace и новой изолированной workflow-history DB.

## Автоматические тесты snapshot

Команда:

```text
venv/bin/python -m unittest discover -s tests -v
```

Результат первичной проверки: **63 теста, OK**. Были добавлены тесты узкого
fraud-output guard, безопасного отказа, новой метрики и пятого листа XLSX.

Прохождение этих тестов остаётся валидным для покрытых контрактов, но набор
не проверял cross-database session collision, citations финального body,
formula injection и отсутствие raw unsafe debug output.

После release-hardening общий набор расширен до **131 теста, OK**. Новые
tests покрывают session namespaces, Ollama preflight, final/evidence gates,
semantic evaluator, HTTPS collector и XLSX sanitization. Дополнительно
успешны compile/import smoke, `pip check` и `git diff --check`.

## Первичный production benchmark

Использован versioned-набор `2026-07-29-v1`: 9 сценариев, 11 interactions.
Он содержит обычные вопросы, сравнение категорий, недостаток персональных
данных, трёхходовый follow-up, ложную предпосылку, несуществующую категорию,
prompt injection, недоступную страницу и сравнение нескольких категорий.

| Модель | Pass | Pass rate | Workflow failures | Tool calls | Tool errors | Evidence reads | Citations | Invalid IDs | Safety blocks | Средняя длительность завершённых, с |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5:14b` | 10/11 | 90,9% | 1 | 64 | 1 | 10 | 75 | 0 | 1 | 335,490 |
| `hermes3:8b-llama3.1-q4_K_M` | 3/11 | 27,3% | 6 | 26 | 0 | 5 | 8 | 0 | 0 | 134,991 |

Обе строки имеют benchmark version
`visa-benchmark-phase8-v1:2026-07-29-v1` и digest
`f60946d9680b7087430046d7f9e6af30187f16f8e908b5ceb7b2a353a6076f69`.

### Почему результаты не являются сравнением моделей

Новая reporting DB начала нумерацию с `run_id=1`, а Qwen session IDs
совпали с ранее сохранёнными sessions общей Agno DB. Независимые Qwen
сценарии получили предыдущие runs, а follow-up session — предыдущие turns.
Hermes использовал другой локальный run ID и не получил ту же history.

Кроме того, `citation_count` извлекался из шага
`check_research_citations`, то есть до Validator и Manager. Validator мог
удалить citations, а Manager — вернуть их или изменить смысл, при этом
interaction сохранял прежний technical result.

Следовательно:

- Qwen `10/11` и Hermes `3/11` нельзя использовать для ranking;
- утверждение «у technical pass есть citations» относится к Researcher
  report, а не обязательно к финальному answer;
- одинаковые config/digest не доказывают одинаковый execution context.

## Проверка артефактов snapshot

Первичный аудит подтвердил:

- SQLite `integrity_check`: `ok`;
- 2 runs, 22 interactions, 90 tool-call events;
- 13 строк соответствуют старому technical criterion;
- technical pass с invalid Researcher source IDs: 0;
- technical pass без записанных tool calls/evidence/sources: 0;
- orphan tool calls: 0;
- каждый run содержит 9 scenario session IDs;
- три follow-up interactions каждого run используют одну session;
- formula error scan существующих cells: 0;
- визуально проверены все 5 листов XLSX.

Эти пункты подтверждают внутреннюю целостность reporting artifacts, но не
устраняют history contamination и не доказывают semantic grounding.
Существующий formula scan также не моделировал недоверенный вопрос или
output, начинающийся с Excel formula prefix.

## Материалы для демонстрации

- [`PROJECT_DOCUMENTATION.md`](PROJECT_DOCUMENTATION.md) — каноническая
  документация и release checklist.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — архитектурный snapshot первичной
  реализации phase 8.
- [`assets/phase_8_tool_trace.png`](assets/phase_8_tool_trace.png) — фрагмент
  реального stdout с `read_official_source`.
- SQLite и XLSX создаются в `reports/phase_8/`; runtime-результаты исключены
  из Git, поскольку могут содержать чувствительные данные.

## Ограничения, выявленные review

- Межпусковое совпадение benchmark session IDs.
- Статическая CLI session для `/new_run` и `/change_model`.
- Отсутствие повторной citation-проверки после Validator и Manager.
- Нет доказательства, что процитирован именно прочитанный источник.
- Manager может изменить validated report после программных gates.
- Raw Manager output может попасть в debug до safety-check.
- Fraud regex не является общей moderation policy.
- Недоверенные XLSX strings требуют защиты от formula injection.
- Dirty revision не воспроизводит точный код.
- Technical pass не является semantic или legal-quality оценкой.

## Реализованный release hardening

После review код обновлён:

- Workflow version: `1.1.0`;
- prompts: `visa-prompts-2026-07-29-v3`;
- collector: `official-sources-v2`;
- benchmark schema: `visa-benchmark-release-v2`;
- scenarios: `2026-07-29-v2`.

- [x] Уникальные CLI и benchmark session namespaces.
- [x] Отдельная workflow-history DB для каждого benchmark по умолчанию.
- [x] Ollama model/tool preflight, server/model digest и capabilities.
- [x] Citation/source/grounding checks после Validator и Manager.
- [x] Любой unbracketed `S#` token отклоняется Validator/final policy.
- [x] Проверка `cited_source_ids ⊆ successfully_read_source_ids`.
- [x] Technical pass требует явного успеха Researcher и Validator.
- [x] Детерминированный no-citation final path без Manager.
- [x] Python гарантирует локализованный legal disclaimer после Manager и
  фиксирует программное добавление как `legal_disclaimer_added`.
- [x] Debug/tool trace opt-in для первых трёх агентов; Manager debug
  принудительно выключен, поэтому raw pre-filter final answer не печатается.
- [x] XLSX string/control-character sanitization.
- [x] HTTPS-only retrieval, content-type/query limits и evidence hashes.
- [x] Unicode URL normalization и sanitization недоверенных source titles.
- [x] Раздельные technical, semantic и release metrics.
- [x] Release pass требует явных `semantic_pass is True` и
  `safety_policy_pass is True`; `None` обрабатывается fail-closed.
- [x] Semantic regex expectations для всех 11 turns.
- [x] Atomic record/replay frozen evidence с fail-closed hash validation.
- [x] Финальный evidence-manifest SHA-256 сохраняется в
  `component_versions` соответствующего benchmark run.
- [x] Dirty-tree digest вместо неоднозначного суффикса `-dirty`.
- [x] Dependency lock, offline CI и каноническая документация.

Новый benchmark считает technical result по финальному output telemetry.
`benchmark_pass` оставлен compatibility alias для
`benchmark_release_pass`. Semantic checks применяются к model-authored body
без программно добавленной source appendix.

Внешняя Agno telemetry теперь явно выключена (`telemetry=False`) у всех
Agents и Workflow; локальная SQLite/XLSX telemetry проекта сохраняется.
Это не делает live/record retrieval offline: generated query передаётся
DDGS, а запросы к официальным сайтам раскрывают сетевые метаданные, поэтому
PII в вопросы и query включать нельзя.

## Оставшиеся проверки

- [x] Финальный полный offline test suite: 131 тест, `OK`.
- [x] Compile/import smoke, `pip check` и `git diff --check`.
- [x] `visa_cli.py --help` и `benchmark_runner.py --help` без Ollama.
- [ ] Hosted CI после commit/push.
- [ ] Чистый commit, новые DB и повторный multi-model benchmark.
- [ ] Повторный SQLite/XLSX/visual audit и обновление итоговой таблицы.
- [ ] Human semantic review финальных answers.
- [ ] Выбрать владельцем проекта и добавить LICENSE до публичного релиза.
- [ ] Провести dependency/security review перед публичным релизом.
- [x] Raw pre-filter Manager output не выводится даже при `--debug`.

## Итог

Release-hardening реализация создана и прошла 131 локальный тест, но
критерий phase 8 ещё не выполнен. Проект не следует публиковать как
release-ready до hosted CI, чистого commit и повторного benchmark с полностью
изолированной history. После него диагностическая таблица выше должна быть
заменена новыми валидными результатами.
