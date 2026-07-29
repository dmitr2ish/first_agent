from __future__ import annotations

import argparse
import sys
import uuid
from typing import Sequence

from agno.utils.pprint import pprint_run_response

import official_sources
from experiment_reporter import ExperimentConfig, ExperimentReporter
from ollama_runtime import (
    DEFAULT_OLLAMA_URL,
    OllamaModelMetadata,
    OllamaPreflightError,
    list_ollama_models,
    preflight_ollama_model,
)
from project_config import (
    DATABASE_FILE,
    MAX_SOURCE_PAGES,
    MODEL_OPTIONS,
    PROMPT_VERSION,
    REPORT_DATABASE_FILE,
    REPORT_XLSX_FILE,
    SOURCE_COLLECTOR_VERSION,
    WORKFLOW_VERSION,
    get_component_versions,
    get_git_commit,
)
from visa_workflow import (
    init_visa_workflow,
    workflow_response_succeeded,
)


def choose_model(
    *,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> OllamaModelMetadata:
    """Get the available local Ollama models and ask the user to choose."""

    print("\n[^-^] Получаю список доступных моделей Ollama...")

    available_models = [
        model["name"]
        for model in list_ollama_models(base_url=ollama_url)
    ]

    if not available_models:
        raise OllamaPreflightError(
            "Нет доступных моделей. Выполните: ollama pull qwen2.5:14b"
        )

    print("\n==== Доступные модели ====")

    for index, model_name in enumerate(available_models, start=1):
        print(f"{index}. {model_name}")

    print("=" * 26)

    while True:
        raw_choice = input("\nВыберите номер модели: ").strip()

        try:
            choice = int(raw_choice)
        except ValueError:
            print("Введите число.")
            continue

        if 1 <= choice <= len(available_models):
            selected_model = available_models[choice - 1]
            metadata = preflight_ollama_model(
                selected_model,
                base_url=ollama_url,
                require_tools=True,
            )
            print(f"Выбрана модель: {selected_model}")
            return metadata

        print("Неверный номер модели.")


def is_greeting(message: str) -> bool:
    normalized = message.casefold().strip()

    greetings = {
        "привет",
        "здравствуйте",
        "добрый день",
        "добрый вечер",
        "hello",
        "hi",
        "hey",
    }

    return normalized in greetings


def build_experiment_config(
        model_name: str,
        *,
        notes: str,
        model_metadata: OllamaModelMetadata,
        ollama_url: str,
) -> ExperimentConfig:
    """Build shared versioned metadata for an interactive experiment run."""

    return ExperimentConfig(
        model_name=model_name,
        model_options=MODEL_OPTIONS,
        workflow_version=WORKFLOW_VERSION,
        prompt_version=PROMPT_VERSION,
        source_collector_version=SOURCE_COLLECTOR_VERSION,
        max_source_pages=MAX_SOURCE_PAGES,
        allowed_domains=list(official_sources.ALLOWED_DOMAINS),
        git_commit=get_git_commit(),
        notes=notes,
        component_versions={
            **get_component_versions(),
            **model_metadata.as_component_versions(),
            "ollama_base_url": ollama_url,
        },
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive US visa research assistant backed by local Ollama."
        ),
    )
    parser.add_argument(
        "--model",
        help=(
            "Installed Ollama model. If omitted, an interactive selector "
            "is shown."
        ),
    )
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama API base URL.",
    )
    parser.add_argument(
        "--workflow-db",
        default=DATABASE_FILE,
        help="SQLite file used for conversation history.",
    )
    parser.add_argument(
        "--report-db",
        default=REPORT_DATABASE_FILE,
        help="SQLite experiment report path.",
    )
    parser.add_argument(
        "--report-xlsx",
        default=REPORT_XLSX_FILE,
        help="Generated XLSX report path.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable verbose Agno/model tracing. Debug output may contain "
            "questions, evidence, and raw model text."
        ),
    )
    return parser


def build_cli_session_id(
    *,
    namespace: str,
    experiment_run_id: int,
) -> str:
    """Build a process- and run-scoped conversation session ID."""

    clean_namespace = "".join(
        character
        for character in namespace.casefold()
        if character.isalnum()
    )

    if not clean_namespace:
        raise ValueError("CLI session namespace cannot be empty.")

    return (
        f"visa-cli-{clean_namespace[:20]}-"
        f"run-{experiment_run_id}"
    )


def _record_cli_failure(
    reporter: ExperimentReporter,
    *,
    experiment_run_id: int,
    question: str,
    error: Exception,
    session_id: str,
) -> None:
    try:
        reporter.record_failure(
            experiment_run_id=experiment_run_id,
            question=question,
            error=error,
            session_id=session_id,
        )
    except Exception as reporting_error:
        print(
            "\n[!] Не удалось сохранить информацию об ошибке: "
            f"{reporting_error}",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(
        list(argv) if argv is not None else []
    )

    try:
        if args.model:
            model_metadata = preflight_ollama_model(
                args.model,
                base_url=args.ollama_url,
                require_tools=True,
            )
            selected_model = model_metadata.name
        else:
            model_metadata = choose_model(
                ollama_url=args.ollama_url,
            )
            selected_model = model_metadata.name
    except OllamaPreflightError as error:
        print(f"[!] {error}", file=sys.stderr)
        return 2

    reporter = ExperimentReporter(
        db_path=args.report_db,
        xlsx_path=args.report_xlsx,
    )
    workflow = init_visa_workflow(
        model_name=selected_model,
        db_file=args.workflow_db,
        debug_mode=args.debug,
        ollama_host=args.ollama_url,
    )

    experiment_run_id = reporter.start_run(
        build_experiment_config(
            selected_model,
            notes="Initial deterministic workflow test",
            model_metadata=model_metadata,
            ollama_url=args.ollama_url,
        )
    )
    session_namespace = uuid.uuid4().hex
    session_id = build_cli_session_id(
        namespace=session_namespace,
        experiment_run_id=experiment_run_id,
    )

    print(
        "\n[Experiment] Начат запуск "
        f"№{reporter.get_run_number(experiment_run_id)}"
    )

    print("\n" + "=" * 60)
    print("ИИ-ассистент по визам и иммиграции США")
    print("Используются официальные государственные источники.")
    print("Команды: /exit, /new_run, /change_model")
    if args.debug:
        print(
            "[Внимание] Debug включён: stdout может содержать "
            "чувствительные данные и raw model output."
        )
    print("=" * 60)

    try:
        while True:
            try:
                user_query = input("\n👤 Ваш запрос:\n> ").strip()
            except EOFError:
                print("\nВвод завершён.")
                break

            if not user_query:
                continue

            command = user_query.casefold()

            if command == "/exit":
                print("\nДо свидания!")
                break

            if command == "/new_run":
                reporter.finish_run(experiment_run_id)

                experiment_run_id = reporter.start_run(
                    build_experiment_config(
                        selected_model,
                        notes="Manual new experiment run",
                        model_metadata=model_metadata,
                        ollama_url=args.ollama_url,
                    )
                )
                session_id = build_cli_session_id(
                    namespace=session_namespace,
                    experiment_run_id=experiment_run_id,
                )

                print(
                    "\n[Experiment] Начат запуск "
                    f"№{reporter.get_run_number(experiment_run_id)}"
                )
                continue

            if command == "/change_model":
                reporter.finish_run(experiment_run_id)

                try:
                    model_metadata = choose_model(
                        ollama_url=args.ollama_url,
                    )
                    selected_model = model_metadata.name
                except OllamaPreflightError as error:
                    print(f"\n[!] {error}", file=sys.stderr)
                    experiment_run_id = reporter.start_run(
                        build_experiment_config(
                            selected_model,
                            notes=(
                                "Run restored after unsuccessful model change"
                            ),
                            model_metadata=model_metadata,
                            ollama_url=args.ollama_url,
                        )
                    )
                    session_id = build_cli_session_id(
                        namespace=session_namespace,
                        experiment_run_id=experiment_run_id,
                    )
                    continue

                workflow = init_visa_workflow(
                    model_name=selected_model,
                    db_file=args.workflow_db,
                    debug_mode=args.debug,
                    ollama_host=args.ollama_url,
                )

                experiment_run_id = reporter.start_run(
                    build_experiment_config(
                        selected_model,
                        notes="Run created after model change",
                        model_metadata=model_metadata,
                        ollama_url=args.ollama_url,
                    )
                )
                session_id = build_cli_session_id(
                    namespace=session_namespace,
                    experiment_run_id=experiment_run_id,
                )

                print(
                    "\n[Experiment] Начат запуск "
                    f"№{reporter.get_run_number(experiment_run_id)}"
                )
                continue

            if is_greeting(user_query):
                print(
                    "\nЗдравствуйте! Задайте вопрос о визах "
                    "или иммиграции США."
                )
                continue

            print("\n[Workflow] Запускаю проверенный pipeline...")

            try:
                response = workflow.run(
                    input=user_query,
                    session_id=session_id,
                    additional_data={
                        "experiment_run_id": experiment_run_id,
                        "model_name": selected_model,
                        "model_options": MODEL_OPTIONS,
                        "workflow_version": WORKFLOW_VERSION,
                        "prompt_version": PROMPT_VERSION,
                        "source_collector_version": SOURCE_COLLECTOR_VERSION,
                    },
                    stream=False,
                )
            except Exception as error:
                _record_cli_failure(
                    reporter,
                    experiment_run_id=experiment_run_id,
                    question=user_query,
                    error=error,
                    session_id=session_id,
                )
                print(f"\n[!] Ошибка Workflow: {error}")
                continue

            reporter.record_workflow_result(
                experiment_run_id=experiment_run_id,
                question=user_query,
                workflow_response=response,
            )

            if not workflow_response_succeeded(response):
                print(
                    "\n[!] Workflow остановлен обязательным шагом: "
                    f"{getattr(response, 'content', '')}"
                )
                continue

            pprint_run_response(
                response,
                markdown=True,
            )

            if reporter.last_export_error:
                print(
                    "\n[Report] SQLite сохранён, но XLSX не обновлён: "
                    f"{reporter.last_export_error}",
                    file=sys.stderr,
                )
            else:
                print(
                    "\n[Report] Результат сохранён в "
                    f"{args.report_xlsx}"
                )

    except KeyboardInterrupt:
        print("\n\nРабота завершена.")
    finally:
        try:
            reporter.finish_run(experiment_run_id)
        except Exception as error:
            print(
                f"\n[!] Не удалось закрыть experiment run: {error}",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
