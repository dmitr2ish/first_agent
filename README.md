# Visa Research Workflow

Локальный multi-agent workflow для исследования вопросов о визах и
иммиграции США. Система ищет документы на разрешённых государственных
доменах, передаёт загруженный evidence package агентам через инструменты,
проверяет source IDs программно и сохраняет benchmark-телеметрию в SQLite и
XLSX.

> Проект находится на этапе release hardening. Он не предоставляет
> юридическую консультацию, не гарантирует право на визу и не должен
> использоваться как единственный источник для иммиграционных решений.

Полное описание проекта, дизайна, архитектуры, контрактов, фаз 0–8,
ограничений и release checklist находится в
[`doc/PROJECT_DOCUMENTATION.md`](doc/PROJECT_DOCUMENTATION.md).

## Быстрый старт

Требования:

- Python 3.12 или новее; CI проверяет Python 3.12;
- локальный [Ollama](https://ollama.com/) и модель с поддержкой tool calling;
- доступ в интернет к поиску и официальным сайтам США для реального запуска.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock

ollama serve
ollama pull qwen2.5:14b
```

В другом терминале:

```bash
source .venv/bin/activate
python visa_cli.py --model qwen2.5:14b
```

Если `--model` не указан, CLI предлагает выбрать установленную
Ollama-модель. Перед запуском выполняется model/tool preflight. Доступные
команды внутри диалога: `/new_run`, `/change_model`, `/exit`.

Изолированные пути и debug задаются явно:

```bash
python visa_cli.py \
  --model qwen2.5:14b \
  --workflow-db reports/local/cli_history.db \
  --report-db reports/local/cli_report.db \
  --report-xlsx reports/local/cli_report.xlsx
```

Подробный Agno/tool trace первых трёх агентов включается только флагом
`--debug` и может содержать чувствительные данные. Manager debug всегда
выключен, чтобы raw final answer не печатался до safety policy.

Внешняя framework telemetry Agno явно выключена (`telemetry=False`) у всех
Agents и Workflow; локальная отчётность проекта в SQLite/XLSX остаётся
включённой. Live retrieval при этом не является offline: поисковый запрос
уходит в DDGS, а HTTPS-запросы к официальным сайтам раскрывают сетевые
метаданные. Не включайте PII в вопросы и поисковые формулировки.

## Тесты

Автоматические тесты не требуют Ollama и сетевого доступа:

```bash
source .venv/bin/activate
python -m pip check
python -m unittest discover -s tests -v
python -m compileall -q \
  benchmark_evaluator.py benchmark_evidence.py benchmark_runner.py \
  experiment_reporter.py official_sources.py ollama_runtime.py project_config.py \
  source_analysis_tools.py visa_cli.py visa_workflow.py tests
```

## Benchmark

Сначала убедитесь, что Ollama запущена и выбранная модель установлена:

```bash
source .venv/bin/activate
python benchmark_runner.py \
  --model qwen2.5:14b \
  --questions benchmark_questions.json \
  --db reports/local/benchmark.db \
  --xlsx reports/local/benchmark.xlsx \
  --workflow-db reports/local/benchmark_history.db
```

SQLite является источником истины, XLSX пересобирается из него. Каталог
`reports/` игнорируется Git, поскольку содержит полные вопросы, ответы,
фрагменты источников и другую потенциально чувствительную runtime-информацию.
Runner по умолчанию создаёт UUID namespace; если `--workflow-db` не задан,
он создаёт отдельную history DB рядом с reporting DB. Для контролируемого
запуска можно явно передать уникальный `--run-namespace`. Явное значение
нормализуется в читаемый префикс с hash-суффиксом: даже длинные значения с
одинаковыми первыми 24 символами остаются разными. Для сравнения моделей
используйте чистый commit и новые reporting/history DB.

Чтобы две модели получили идентичные официальные документы, сначала
запишите, затем воспроизведите frozen evidence:

```bash
python benchmark_runner.py \
  --model qwen2.5:14b \
  --db reports/record/report.db \
  --xlsx reports/record/report.xlsx \
  --evidence-mode record \
  --evidence-cache reports/evidence/visa-benchmark.json

python benchmark_runner.py \
  --model another-tool-model \
  --db reports/replay/report.db \
  --xlsx reports/replay/report.xlsx \
  --evidence-mode replay \
  --evidence-cache reports/evidence/visa-benchmark.json
```

Evidence manifest привязан к benchmark/workflow/collector versions,
benchmark digest и prompt hashes, проверяет package/manifest SHA-256 и
отклоняет incomplete или изменённый cache. После record finalize или replay
validation финальный manifest SHA-256 сохраняется в
`component_versions.benchmark_evidence_manifest_sha256` соответствующего
benchmark run.

Benchmark возвращает `0` только если каждый turn прошёл Workflow и release
gates; любой workflow/release failure даёт ненулевой exit code.

## Основные файлы

| Путь | Назначение |
|---|---|
| `visa_workflow.py` | агенты, контроллеры и шесть шагов Workflow |
| `official_sources.py` | обязательный поиск и загрузка официальных страниц |
| `source_analysis_tools.py` | инструменты Researcher и Validator |
| `experiment_reporter.py` | SQLite schema, метрики и XLSX export |
| `visa_cli.py` | интерактивный интерфейс |
| `benchmark_runner.py` | versioned benchmark runner |
| `benchmark_evaluator.py` | детерминированные semantic expectations |
| `benchmark_evidence.py` | атомарный record/replay frozen evidence |
| `ollama_runtime.py` | Ollama/model/tool preflight и metadata |
| `benchmark_questions.json` | сценарии benchmark |
| `project_config.py` | версии и общая конфигурация |
| `tests/` | offline unit/integration tests |
| `doc/` | каноническая документация и отчёты фаз |

## Статус релиза

Фазы 1–7 реализованы и подтверждены тестами в их исходном scope. После
итогового ревью реализован release-hardening: изоляция history,
post-Validator/final grounding gates, XLSX sanitization, debug opt-in,
гарантированный Python-кодом legal disclaimer, model preflight, frozen
evidence record/replay и раздельные
technical/semantic/release metrics. Фаза 8 остаётся `[~]` до чистого
повторного multi-model benchmark и проверки его артефактов. Текущий статус и
порядок действий зафиксированы в
[`doc/WORKFLOW_REFACTOR_PLAN.md`](doc/WORKFLOW_REFACTOR_PLAN.md).

История изменений ведётся в [`CHANGELOG.md`](CHANGELOG.md).
