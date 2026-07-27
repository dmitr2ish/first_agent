from __future__ import annotations

import requests

from agno.utils.pprint import pprint_run_response

import official_sources
from experiment_reporter import ExperimentConfig, ExperimentReporter
from project_config import (
    MAX_SOURCE_PAGES,
    MODEL_OPTIONS,
    PROMPT_VERSION,
    REPORT_DATABASE_FILE,
    REPORT_XLSX_FILE,
    SESSION_ID,
    SOURCE_COLLECTOR_VERSION,
    WORKFLOW_VERSION,
    get_git_commit,
)
from visa_workflow import init_visa_workflow


def choose_model() -> str:
    """Get the available local Ollama models and ask the user to choose."""

    print("\n[^-^] Получаю список доступных моделей Ollama...")

    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=10)
        response.raise_for_status()

        models_data = response.json().get("models", [])
        available_models = [
            model["name"]
            for model in models_data
            if model.get("name")
        ]

        if not available_models:
            raise RuntimeError(
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
                print(f"Выбрана модель: {selected_model}")
                return selected_model

            print("Неверный номер модели.")

    except requests.RequestException as error:
        raise RuntimeError(
            "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена."
        ) from error


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
    )


def main() -> None:
    selected_model = choose_model()

    reporter = ExperimentReporter(
        db_path=REPORT_DATABASE_FILE,
        xlsx_path=REPORT_XLSX_FILE,
    )

    experiment_run_id = reporter.start_run(
        build_experiment_config(
            selected_model,
            notes="Initial deterministic workflow test",
        )
    )

    print(
        "\n[Experiment] Начат запуск "
        f"№{reporter.get_run_number(experiment_run_id)}"
    )

    workflow = init_visa_workflow(model_name=selected_model)

    print("\n" + "=" * 60)
    print("ИИ-ассистент по визам и иммиграции США")
    print("Используются официальные государственные источники.")
    print("Команды: /exit, /change_model")
    print("=" * 60)

    while True:
        try:
            user_query = input("\n👤 Ваш запрос:\n> ").strip()

            if not user_query:
                continue

            command = user_query.casefold()

            if command == "/exit":
                reporter.finish_run(experiment_run_id)
                print("\nДо свидания!")
                break

            if command == "/new_run":
                reporter.finish_run(experiment_run_id)

                experiment_run_id = reporter.start_run(
                    build_experiment_config(
                        selected_model,
                        notes="Manual new experiment run",
                    )
                )

                print(
                    "\n[Experiment] Начат запуск "
                    f"№{reporter.get_run_number(experiment_run_id)}"
                )
                continue

            if command == "/change_model":
                reporter.finish_run(experiment_run_id)

                selected_model = choose_model()
                workflow = init_visa_workflow(model_name=selected_model)

                experiment_run_id = reporter.start_run(
                    build_experiment_config(
                        selected_model,
                        notes="Run created after model change",
                    )
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

            response = workflow.run(
                input=user_query,
                session_id=SESSION_ID,
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

            reporter.record_workflow_result(
                experiment_run_id=experiment_run_id,
                question=user_query,
                workflow_response=response,
            )

            pprint_run_response(
                response,
                markdown=True,
            )

            print(
                "\n[Report] Результат сохранён в "
                f"{REPORT_XLSX_FILE}"
            )

        except KeyboardInterrupt:
            reporter.finish_run(experiment_run_id)
            print("\n\nРабота завершена.")
            break

        except Exception as error:
            print(f"\n[!] Ошибка Workflow: {error}")


if __name__ == "__main__":
    main()
