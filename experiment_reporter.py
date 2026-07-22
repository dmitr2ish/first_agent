from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


@dataclass(frozen=True)
class ExperimentConfig:
    """
    Конфигурация одного экспериментального запуска.

    Новый run создаётся при запуске программы или по команде /new_run.
    Все вопросы до следующего /new_run относятся к одной конфигурации.
    """

    model_name: str
    model_options: dict[str, Any]
    workflow_version: str
    prompt_version: str
    source_collector_version: str
    max_source_pages: int
    allowed_domains: list[str]
    git_commit: str
    notes: str = ""


class ExperimentReporter:
    """
    SQLite — основной источник данных.
    XLSX — автоматически пересоздаваемый сравнительный отчёт.
    """

    def __init__(
        self,
        db_path: str = "experiment_results.db",
        xlsx_path: str = "visa_model_comparison.xlsx",
    ) -> None:
        self.db_path = Path(db_path)
        self.xlsx_path = Path(xlsx_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.xlsx_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialize_database()

    # =========================================================
    # DATABASE
    # =========================================================

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row

        # SQLite может хранить несколько читателей и одного писателя.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")

        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_number INTEGER NOT NULL UNIQUE,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT,
                    model_name TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_run_id INTEGER NOT NULL,
                    question_number INTEGER NOT NULL,
                    workflow_run_id TEXT,
                    session_id TEXT,
                    created_at_utc TEXT NOT NULL,

                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,

                    status TEXT NOT NULL,
                    duration_seconds REAL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    time_to_first_token REAL,

                    sources_json TEXT NOT NULL DEFAULT '[]',
                    step_outputs_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,

                    FOREIGN KEY (experiment_run_id)
                        REFERENCES experiment_runs(id)
                        ON DELETE CASCADE,

                    UNIQUE (experiment_run_id, question_number)
                );

                CREATE INDEX IF NOT EXISTS idx_interactions_run
                    ON interactions(experiment_run_id);

                CREATE INDEX IF NOT EXISTS idx_interactions_workflow_run
                    ON interactions(workflow_run_id);
                """
            )

    # =========================================================
    # EXPERIMENT RUNS
    # =========================================================

    def start_run(self, config: ExperimentConfig) -> int:
        """
        Создаёт новую строку запуска и возвращает внутренний run_id.
        """

        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            next_number = connection.execute(
                """
                SELECT COALESCE(MAX(run_number), 0) + 1
                FROM experiment_runs
                """
            ).fetchone()[0]

            cursor = connection.execute(
                """
                INSERT INTO experiment_runs (
                    run_number,
                    started_at_utc,
                    model_name,
                    configuration_json,
                    notes
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    next_number,
                    now,
                    config.model_name,
                    json.dumps(
                        asdict(config),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    config.notes,
                ),
            )

            experiment_run_id = int(cursor.lastrowid)

        self.export_xlsx()

        return experiment_run_id

    def finish_run(self, experiment_run_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE experiment_runs
                SET finished_at_utc = ?
                WHERE id = ?
                """,
                (now, experiment_run_id),
            )

        self.export_xlsx()

    def get_run_number(self, experiment_run_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT run_number
                FROM experiment_runs
                WHERE id = ?
                """,
                (experiment_run_id,),
            ).fetchone()

        if row is None:
            raise ValueError(
                f"Experiment run {experiment_run_id} does not exist"
            )

        return int(row["run_number"])

    def _next_question_number(
        self,
        experiment_run_id: int,
    ) -> int:
        with self._connect() as connection:
            value = connection.execute(
                """
                SELECT COALESCE(MAX(question_number), 0) + 1
                FROM interactions
                WHERE experiment_run_id = ?
                """,
                (experiment_run_id,),
            ).fetchone()[0]

        return int(value)

    # =========================================================
    # RECORDING
    # =========================================================

    def record_workflow_result(
        self,
        *,
        experiment_run_id: int,
        question: str,
        workflow_response: Any,
        sources: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        """
        Сохраняет завершённый WorkflowRunOutput.

        workflow_response ожидается как результат:
            workflow.run(..., stream=False)
        """

        question_number = self._next_question_number(
            experiment_run_id
        )

        answer = self._stringify(
            getattr(workflow_response, "content", "")
        )

        workflow_run_id = getattr(
            workflow_response,
            "run_id",
            None,
        )

        session_id = getattr(
            workflow_response,
            "session_id",
            None,
        )

        status = self._stringify(
            getattr(workflow_response, "status", "unknown")
        )

        metrics = getattr(
            workflow_response,
            "metrics",
            None,
        )

        metrics_dict = self._object_to_dict(metrics)

        step_outputs = self._extract_step_outputs(
            getattr(workflow_response, "step_results", None)
        )

        extracted_sources = (
            sources
            if sources is not None
            else self._find_sources_in_steps(step_outputs)
        )

        input_tokens = self._to_optional_int(
            metrics_dict.get("input_tokens")
        )
        output_tokens = self._to_optional_int(
            metrics_dict.get("output_tokens")
        )
        total_tokens = self._to_optional_int(
            metrics_dict.get("total_tokens")
        )

        duration_seconds = self._to_optional_float(
            metrics_dict.get("duration")
        )
        time_to_first_token = self._to_optional_float(
            metrics_dict.get("time_to_first_token")
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interactions (
                    experiment_run_id,
                    question_number,
                    workflow_run_id,
                    session_id,
                    created_at_utc,
                    question,
                    answer,
                    status,
                    duration_seconds,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    time_to_first_token,
                    sources_json,
                    step_outputs_json,
                    metrics_json,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_run_id,
                    question_number,
                    workflow_run_id,
                    session_id,
                    datetime.now(timezone.utc).isoformat(),
                    question,
                    answer,
                    status,
                    duration_seconds,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    time_to_first_token,
                    json.dumps(
                        extracted_sources,
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        step_outputs,
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        metrics_dict,
                        ensure_ascii=False,
                    ),
                    error,
                ),
            )

        # После каждого вопроса Excel обновляется.
        self.export_xlsx()

    def record_failure(
        self,
        *,
        experiment_run_id: int,
        question: str,
        error: Exception | str,
    ) -> None:
        question_number = self._next_question_number(
            experiment_run_id
        )

        error_text = str(error)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interactions (
                    experiment_run_id,
                    question_number,
                    created_at_utc,
                    question,
                    answer,
                    status,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_run_id,
                    question_number,
                    datetime.now(timezone.utc).isoformat(),
                    question,
                    "",
                    "failed",
                    error_text,
                ),
            )

        self.export_xlsx()

    # =========================================================
    # EXTRACTION
    # =========================================================

    @staticmethod
    def _object_to_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        to_dict = getattr(value, "to_dict", None)

        if callable(to_dict):
            result = to_dict()
            return result if isinstance(result, dict) else {}

        model_dump = getattr(value, "model_dump", None)

        if callable(model_dump):
            result = model_dump()
            return result if isinstance(result, dict) else {}

        if hasattr(value, "__dict__"):
            return {
                key: item
                for key, item in vars(value).items()
                if not key.startswith("_")
            }

        return {}

    def _extract_step_outputs(
        self,
        step_results: Any,
    ) -> dict[str, Any]:
        output: dict[str, Any] = {}

        if not step_results:
            return output

        def visit(items: Iterable[Any]) -> None:
            for item in items:
                if isinstance(item, list):
                    visit(item)
                    continue

                name = (
                    getattr(item, "step_name", None)
                    or getattr(item, "step_id", None)
                    or f"step_{len(output) + 1}"
                )

                output[str(name)] = {
                    "success": getattr(item, "success", None),
                    "error": getattr(item, "error", None),
                    "content": self._stringify(
                        getattr(item, "content", None)
                    ),
                    "metrics": self._object_to_dict(
                        getattr(item, "metrics", None)
                    ),
                }

        visit(step_results)

        return output

    @staticmethod
    def _find_sources_in_steps(
        step_outputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Пытается найти sources в JSON-результате шага
        collect_official_sources.
        """

        likely_names = (
            "collect_official_sources",
            "collect_sources",
            "source_collector",
        )

        for name in likely_names:
            step = step_outputs.get(name)

            if not step:
                continue

            content = step.get("content", "")

            try:
                parsed = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                continue

            sources = parsed.get("sources")

            if isinstance(sources, list):
                return sources

        return []

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return value

        if isinstance(value, (dict, list)):
            return json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
            )

        return str(value)

    @staticmethod
    def _to_optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    # =========================================================
    # XLSX EXPORT
    # =========================================================

    def export_xlsx(self) -> None:
        """
        Не изменяет Excel вручную строка за строкой.

        Каждый раз читает полные данные из SQLite и строит XLSX заново.
        Поэтому SQLite остаётся единственным источником истины.
        """

        runs, interactions = self._load_report_data()

        workbook = Workbook()

        runs_sheet = workbook.active
        runs_sheet.title = "Runs"

        details_sheet = workbook.create_sheet("Interactions")
        steps_sheet = workbook.create_sheet("Step Outputs")

        self._write_runs_sheet(
            runs_sheet,
            runs,
            interactions,
        )

        self._write_interactions_sheet(
            details_sheet,
            runs,
            interactions,
        )

        self._write_steps_sheet(
            steps_sheet,
            runs,
            interactions,
        )

        self._save_atomically(workbook)

    def _load_report_data(
        self,
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        with self._connect() as connection:
            runs = connection.execute(
                """
                SELECT *
                FROM experiment_runs
                ORDER BY run_number
                """
            ).fetchall()

            interactions = connection.execute(
                """
                SELECT *
                FROM interactions
                ORDER BY experiment_run_id, question_number
                """
            ).fetchall()

        return runs, interactions

    def _write_runs_sheet(
        self,
        sheet: Any,
        runs: list[sqlite3.Row],
        interactions: list[sqlite3.Row],
    ) -> None:
        by_run: dict[int, list[sqlite3.Row]] = {}

        for interaction in interactions:
            by_run.setdefault(
                interaction["experiment_run_id"],
                [],
            ).append(interaction)

        max_questions = max(
            (
                len(items)
                for items in by_run.values()
            ),
            default=0,
        )

        headers = [
            "Run",
            "Started UTC",
            "Finished UTC",
            "Model",
            "Configuration",
            "Notes",
        ]

        for number in range(1, max_questions + 1):
            headers.extend(
                [
                    f"Q{number}",
                    f"A{number}",
                    f"Duration {number}",
                    f"Tokens {number}",
                    f"Sources {number}",
                    f"Status {number}",
                ]
            )

        sheet.append(headers)

        for run in runs:
            row: list[Any] = [
                run["run_number"],
                run["started_at_utc"],
                run["finished_at_utc"],
                run["model_name"],
                run["configuration_json"],
                run["notes"],
            ]

            run_interactions = by_run.get(run["id"], [])

            for interaction in run_interactions:
                row.extend(
                    [
                        interaction["question"],
                        interaction["answer"],
                        interaction["duration_seconds"],
                        interaction["total_tokens"],
                        self._source_urls(
                            interaction["sources_json"]
                        ),
                        interaction["status"],
                    ]
                )

            missing = max_questions - len(run_interactions)

            for _ in range(missing):
                row.extend(["", "", "", "", "", ""])

            sheet.append(row)

        self._format_sheet(
            sheet,
            freeze_cell="A2",
            answer_columns=True,
        )

    def _write_interactions_sheet(
        self,
        sheet: Any,
        runs: list[sqlite3.Row],
        interactions: list[sqlite3.Row],
    ) -> None:
        run_by_id = {
            run["id"]: run
            for run in runs
        }

        headers = [
            "Run",
            "Question #",
            "Model",
            "Workflow Run ID",
            "Session ID",
            "Created UTC",
            "Question",
            "Answer",
            "Status",
            "Duration",
            "Input Tokens",
            "Output Tokens",
            "Total Tokens",
            "Time To First Token",
            "Sources",
            "Error",
        ]

        sheet.append(headers)

        for interaction in interactions:
            run = run_by_id[interaction["experiment_run_id"]]

            sheet.append(
                [
                    run["run_number"],
                    interaction["question_number"],
                    run["model_name"],
                    interaction["workflow_run_id"],
                    interaction["session_id"],
                    interaction["created_at_utc"],
                    interaction["question"],
                    interaction["answer"],
                    interaction["status"],
                    interaction["duration_seconds"],
                    interaction["input_tokens"],
                    interaction["output_tokens"],
                    interaction["total_tokens"],
                    interaction["time_to_first_token"],
                    self._source_urls(
                        interaction["sources_json"]
                    ),
                    interaction["error"],
                ]
            )

        self._format_sheet(
            sheet,
            freeze_cell="A2",
            answer_columns=True,
        )

    def _write_steps_sheet(
        self,
        sheet: Any,
        runs: list[sqlite3.Row],
        interactions: list[sqlite3.Row],
    ) -> None:
        run_by_id = {
            run["id"]: run
            for run in runs
        }

        sheet.append(
            [
                "Run",
                "Question #",
                "Model",
                "Step",
                "Success",
                "Error",
                "Content",
                "Metrics",
            ]
        )

        for interaction in interactions:
            run = run_by_id[interaction["experiment_run_id"]]

            try:
                steps = json.loads(
                    interaction["step_outputs_json"] or "{}"
                )
            except json.JSONDecodeError:
                steps = {}

            for step_name, step_data in steps.items():
                sheet.append(
                    [
                        run["run_number"],
                        interaction["question_number"],
                        run["model_name"],
                        step_name,
                        step_data.get("success"),
                        step_data.get("error"),
                        step_data.get("content"),
                        json.dumps(
                            step_data.get("metrics", {}),
                            ensure_ascii=False,
                        ),
                    ]
                )

        self._format_sheet(
            sheet,
            freeze_cell="A2",
            answer_columns=True,
        )

    @staticmethod
    def _source_urls(sources_json: str) -> str:
        try:
            sources = json.loads(sources_json or "[]")
        except json.JSONDecodeError:
            return ""

        urls: list[str] = []

        for source in sources:
            if not isinstance(source, dict):
                continue

            source_id = source.get("source_id", "")
            url = source.get("url", "")

            if url:
                urls.append(
                    f"{source_id}: {url}"
                    if source_id
                    else url
                )

        return "\n".join(urls)

    @staticmethod
    def _format_sheet(
        sheet: Any,
        *,
        freeze_cell: str,
        answer_columns: bool,
    ) -> None:
        sheet.freeze_panes = freeze_cell
        sheet.auto_filter.ref = sheet.dimensions

        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=True,
                )

        for index, cell in enumerate(sheet[1], start=1):
            header = str(cell.value or "")

            if header.startswith(("Q", "A")):
                width = 55
            elif header in {
                "Question",
                "Answer",
                "Content",
                "Configuration",
                "Metrics",
                "Sources",
            }:
                width = 70
            else:
                width = min(max(len(header) + 3, 12), 28)

            sheet.column_dimensions[
                get_column_letter(index)
            ].width = width

    def _save_atomically(self, workbook: Workbook) -> None:
        """
        Сначала сохраняет временный XLSX, затем заменяет отчёт.
        Старый отчёт не уничтожается при ошибке записи.
        """

        target_directory = self.xlsx_path.parent

        with tempfile.NamedTemporaryFile(
            prefix="visa_report_",
            suffix=".xlsx",
            dir=target_directory,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        try:
            workbook.save(temporary_path)
            os.replace(
                temporary_path,
                self.xlsx_path,
            )
        finally:
            if temporary_path.exists():
                temporary_path.unlink()