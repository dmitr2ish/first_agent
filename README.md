# Visa Research Workflow

Visa Research Workflow — локальный multi-agent pipeline для исследования
вопросов о визах и иммиграции США. Он ищет материалы только на разрешённых
официальных сайтах, заставляет исследующего и проверяющего агентов читать
загруженные документы через инструменты, проверяет цитаты Python-кодом и
сохраняет результаты запусков в SQLite и XLSX.

Проект предназначен для исследования и сравнения локальных LLM. Он не
предоставляет юридическую консультацию и не заменяет официальные инструкции
ведомств или помощь лицензированного специалиста.

## Как устроен проект

В корне находятся только две исполняемые точки входа:

- `visa_cli.py` — интерактивный диалог;
- `benchmark_runner.py` — воспроизводимый прогон набора сценариев.

Остальной код собран в пакете `visa_research`:

```text
visa_research/
├── cli.py                 # интерактивный интерфейс
├── config.py              # общие версии и настройки
├── runtime.py             # проверка Ollama и выбранной модели
├── workflow.py            # агенты, шаги и программные проверки
├── reporting.py           # SQLite и XLSX
├── sources/
│   ├── collector.py       # поиск и загрузка официальных документов
│   └── tools.py           # инструменты чтения evidence для агентов
└── benchmark/
    ├── runner.py          # выполнение сценариев
    ├── evaluator.py       # детерминированные semantic-проверки
    └── evidence.py        # record/replay одинаковых источников
```

Дополнительные каталоги:

- `doc/` — описание проекта, дизайн и архитектура;
- `resources/` — benchmark-сценарии и файлы зависимостей;
- `scripts/` — готовые сценарии запуска;
- `tests/` — offline unit- и integration-тесты.

## Быстрый старт

Требуются Python 3.12+, локальный Ollama, модель с поддержкой tool calling и
интернет-доступ к официальным сайтам США.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r resources/requirements.lock

ollama serve
ollama pull qwen2.5:14b
```

Запуск интерактивного режима:

```bash
python visa_cli.py --model qwen2.5:14b
```

Или через готовый сценарий:

```bash
scripts/run_cli.sh --model qwen2.5:14b
```

Если модель не указана, приложение предложит выбрать её из локально
установленных моделей. Команды диалога:

- `/new_run` — начать новый независимый experiment run;
- `/change_model` — выбрать другую модель;
- `/exit` — завершить работу.

## Benchmark

Встроенный набор находится в `resources/benchmark_questions.json`:

```bash
python benchmark_runner.py \
  --model qwen2.5:14b \
  --db reports/local/benchmark.db \
  --xlsx reports/local/benchmark.xlsx \
  --workflow-db reports/local/benchmark_history.db
```

Эквивалентный сценарий:

```bash
scripts/run_benchmark.sh --model qwen2.5:14b
```

Для сравнения моделей на одинаковых документах используйте evidence
record/replay:

```bash
python benchmark_runner.py \
  --model qwen2.5:14b \
  --evidence-mode record \
  --evidence-cache reports/evidence/benchmark.json \
  --db reports/model-a/report.db \
  --xlsx reports/model-a/report.xlsx

python benchmark_runner.py \
  --model another-tool-model \
  --evidence-mode replay \
  --evidence-cache reports/evidence/benchmark.json \
  --db reports/model-b/report.db \
  --xlsx reports/model-b/report.xlsx
```

SQLite является источником истины. XLSX полностью пересобирается из SQLite и
предназначен для просмотра результатов. Каталог `reports/` игнорируется Git,
поскольку может содержать вопросы, ответы и фрагменты источников.

## Проверка проекта

Тесты не используют Ollama и сеть:

```bash
scripts/run_tests.sh
```

Или напрямую:

```bash
python -m pip check
python -m compileall -q benchmark_runner.py visa_cli.py visa_research tests
python -m unittest discover -s tests -v
```

## Документация

- [Обзор и руководство](doc/OVERVIEW.md)
- [Дизайн и гарантии](doc/DESIGN.md)
- [Архитектура и поток данных](doc/ARCHITECTURE.md)

Начать знакомство лучше с [индекса документации](doc/README.md).
