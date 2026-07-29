from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agno.workflow.step import StepOutput

from visa_research.benchmark import runner as benchmark_runner
from visa_research.benchmark.evidence import (
    BenchmarkEvidenceCache,
    EvidenceCacheError,
    EvidenceTurn,
    extract_source_package,
)
from visa_research.workflow import validate_source_package_override


def source_package(label: str = "recorded") -> dict:
    content = f"{label} exact content"
    source = {
        "source_id": "S1",
        "title": f"{label} title",
        "url": "https://www.uscis.gov/example",
        "document_type": "html",
        "content_type": "text/html",
        "content": content,
        "content_sha256": hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest(),
    }
    digest_payload = [
        {
            "source_id": source["source_id"],
            "url": source["url"],
            "content_sha256": source["content_sha256"],
        }
    ]
    evidence_package_digest = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "query": "official visa evidence",
        "source_count": 1,
        "evidence_package_digest": evidence_package_digest,
        "sources": [source],
        "errors": [],
    }


def workflow_response(package: dict) -> SimpleNamespace:
    return SimpleNamespace(
        content="Answer [S1]",
        run_id="workflow-run",
        session_id="session",
        status="completed",
        metrics={},
        step_results=[
            StepOutput(
                step_name="collect_official_sources",
                content=json.dumps(package),
                success=True,
            ),
            StepOutput(
                step_name="create_final_answer",
                content="Answer [S1]",
                success=True,
            ),
        ],
    )


def benchmark_config(
    *,
    digest: str = "benchmark-digest",
) -> benchmark_runner.BenchmarkConfig:
    return benchmark_runner.BenchmarkConfig(
        version="benchmark-v1",
        digest=digest,
        scenarios=(
            benchmark_runner.BenchmarkScenario(
                scenario_id="first",
                title="First",
                scenario_type="independent",
                turns=(
                    benchmark_runner.BenchmarkTurn(
                        turn_id="question",
                        prompt="First question?",
                    ),
                ),
            ),
            benchmark_runner.BenchmarkScenario(
                scenario_id="follow-up",
                title="Follow-up",
                scenario_type="follow_up",
                turns=(
                    benchmark_runner.BenchmarkTurn(
                        turn_id="base",
                        prompt="Base question?",
                    ),
                    benchmark_runner.BenchmarkTurn(
                        turn_id="follow",
                        prompt="And O-1?",
                    ),
                ),
            ),
        ),
    )


class EvidenceWorkflow:
    def __init__(self, package: dict) -> None:
        self.package = package
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return workflow_response(self.package)


class BenchmarkEvidenceTests(unittest.TestCase):
    def test_extracts_exact_collector_step_output(self) -> None:
        package = source_package()
        response = workflow_response(package)
        response.step_results = [[response.step_results]]

        extracted = extract_source_package(response)

        self.assertEqual(extracted, package)
        self.assertIsNot(extracted, package)
        self.assertEqual(
            validate_source_package_override(extracted),
            package,
        )

    def test_extraction_rejects_missing_or_invalid_collector_output(
            self,
    ) -> None:
        with self.assertRaisesRegex(
            EvidenceCacheError,
            "no collect_official_sources",
        ):
            extract_source_package(
                SimpleNamespace(step_results=[])
            )

    def test_valid_empty_package_can_be_frozen_for_no_source_scenario(
            self,
    ) -> None:
        empty_package = {
            "query": "hypothetical visa category",
            "source_count": 0,
            "evidence_package_digest": hashlib.sha256(
                b"[]"
            ).hexdigest(),
            "sources": [],
            "errors": ["No official pages were available."],
        }
        config = benchmark_runner.BenchmarkConfig(
            version="benchmark-v1",
            digest="digest",
            scenarios=(
                benchmark_runner.BenchmarkScenario(
                    scenario_id="no-sources",
                    title="No sources",
                    scenario_type="independent",
                    turns=(
                        benchmark_runner.BenchmarkTurn(
                            turn_id="question",
                            prompt="What is Z-9?",
                        ),
                    ),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "empty-evidence.json"
            record_cache = benchmark_runner.build_evidence_cache(
                mode="record",
                path=cache_path,
                config=config,
            )
            turn = EvidenceTurn.from_prompt(
                scenario_id="no-sources",
                turn_id="question",
                prompt="What is Z-9?",
            )
            record_cache.record_response(
                turn=turn,
                workflow_response=workflow_response(empty_package),
            )
            record_cache.finalize()

            replay_cache = benchmark_runner.build_evidence_cache(
                mode="replay",
                path=cache_path,
                config=config,
            )
            self.assertEqual(
                replay_cache.source_package_for(turn=turn),
                empty_package,
            )

        with self.assertRaisesRegex(
            EvidenceCacheError,
            "not valid JSON",
        ):
            extract_source_package(
                SimpleNamespace(
                    step_results=[
                        StepOutput(
                            step_name="collect_official_sources",
                            content="{broken",
                        )
                    ]
                )
            )

    def test_record_then_replay_injects_identical_packages(self) -> None:
        config = benchmark_config()
        recorded_package = source_package("frozen")

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "evidence.json"
            record_cache = benchmark_runner.build_evidence_cache(
                mode="record",
                path=cache_path,
                config=config,
            )
            self.assertIsNotNone(record_cache)
            record_workflow = EvidenceWorkflow(recorded_package)
            record_reporter = Mock()

            benchmark_runner.run_benchmark(
                workflow=record_workflow,
                reporter=record_reporter,
                experiment_run_id=1,
                config=config,
                run_namespace="record-run",
                evidence_cache=record_cache,
            )

            manifest = json.loads(
                cache_path.read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["complete"])
            self.assertEqual(len(manifest["entries"]), 3)
            self.assertEqual(
                manifest["benchmark_digest"],
                config.digest,
            )

            replay_cache = benchmark_runner.build_evidence_cache(
                mode="replay",
                path=cache_path,
                config=config,
            )
            replay_workflow = EvidenceWorkflow(
                source_package("should-not-be-used")
            )

            benchmark_runner.run_benchmark(
                workflow=replay_workflow,
                reporter=Mock(),
                experiment_run_id=2,
                config=config,
                run_namespace="replay-model-a",
                evidence_cache=replay_cache,
            )

            overrides = [
                call["additional_data"]["source_package_override"]
                for call in replay_workflow.calls
            ]
            self.assertEqual(
                overrides,
                [recorded_package, recorded_package, recorded_package],
            )
            self.assertEqual(
                len({id(package) for package in overrides}),
                3,
            )

            # The same cache is intentionally not bound to a model name.
            second_replay = benchmark_runner.build_evidence_cache(
                mode="replay",
                path=cache_path,
                config=config,
            )
            self.assertEqual(
                second_replay.manifest_sha256,
                replay_cache.manifest_sha256,
            )

    def test_replay_rejects_tampered_package(self) -> None:
        config = benchmark_runner.BenchmarkConfig(
            version="benchmark-v1",
            digest="digest",
            scenarios=(
                benchmark_runner.BenchmarkScenario(
                    scenario_id="one",
                    title="One",
                    scenario_type="independent",
                    turns=(
                        benchmark_runner.BenchmarkTurn(
                            turn_id="question",
                            prompt="Question?",
                        ),
                    ),
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "evidence.json"
            record_cache = benchmark_runner.build_evidence_cache(
                mode="record",
                path=cache_path,
                config=config,
            )
            turn = EvidenceTurn.from_prompt(
                scenario_id="one",
                turn_id="question",
                prompt="Question?",
            )
            record_cache.record_response(
                turn=turn,
                workflow_response=workflow_response(source_package()),
            )
            record_cache.finalize()

            manifest = json.loads(
                cache_path.read_text(encoding="utf-8")
            )
            entry = next(iter(manifest["entries"].values()))
            entry["source_package"]["sources"][0]["content"] = "tampered"
            cache_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                EvidenceCacheError,
                "manifest hash mismatch",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=config,
                )

    def test_replay_rejects_digest_and_version_mismatch(self) -> None:
        config = benchmark_config()

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "evidence.json"
            record_cache = benchmark_runner.build_evidence_cache(
                mode="record",
                path=cache_path,
                config=config,
            )
            benchmark_runner.run_benchmark(
                workflow=EvidenceWorkflow(source_package()),
                reporter=Mock(),
                experiment_run_id=1,
                config=config,
                run_namespace="record-mismatch-test",
                evidence_cache=record_cache,
            )

            digest_mismatch = benchmark_runner.BenchmarkConfig(
                version=config.version,
                digest="different-digest",
                scenarios=config.scenarios,
            )
            with self.assertRaisesRegex(
                EvidenceCacheError,
                "benchmark digest mismatch",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=digest_mismatch,
                )

            version_mismatch = benchmark_runner.BenchmarkConfig(
                version="different-version",
                digest=config.digest,
                scenarios=config.scenarios,
            )
            with self.assertRaisesRegex(
                EvidenceCacheError,
                "benchmark version mismatch",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=version_mismatch,
                )

            with self.assertRaisesRegex(
                EvidenceCacheError,
                "source collector version mismatch",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=config,
                    source_collector_version="different-collector",
                )

            with self.assertRaisesRegex(
                EvidenceCacheError,
                "workflow version mismatch",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=config,
                    workflow_version="different-workflow",
                )

    def test_replay_rejects_incomplete_cache(self) -> None:
        config = benchmark_config()

        with tempfile.TemporaryDirectory() as temp_directory:
            cache_path = Path(temp_directory) / "evidence.json"
            record_cache = benchmark_runner.build_evidence_cache(
                mode="record",
                path=cache_path,
                config=config,
            )
            first_turn = config.scenarios[0].turns[0]
            record_cache.record_response(
                turn=EvidenceTurn.from_prompt(
                    scenario_id=config.scenarios[0].scenario_id,
                    turn_id=first_turn.turn_id,
                    prompt=first_turn.prompt,
                ),
                workflow_response=workflow_response(source_package()),
            )

            with self.assertRaisesRegex(
                EvidenceCacheError,
                "missing benchmark turns",
            ):
                record_cache.finalize()

            with self.assertRaisesRegex(
                EvidenceCacheError,
                "incomplete",
            ):
                benchmark_runner.build_evidence_cache(
                    mode="replay",
                    path=cache_path,
                    config=config,
                )

            follow_up = config.scenarios[1]
            second_turn = follow_up.turns[0]
            record_cache.record_response(
                turn=EvidenceTurn.from_prompt(
                    scenario_id=follow_up.scenario_id,
                    turn_id=second_turn.turn_id,
                    prompt=second_turn.prompt,
                ),
                workflow_response=workflow_response(source_package("two")),
            )
            still_incomplete = json.loads(
                cache_path.read_text(encoding="utf-8")
            )
            self.assertFalse(still_incomplete["complete"])

            third_turn = follow_up.turns[1]
            record_cache.record_response(
                turn=EvidenceTurn.from_prompt(
                    scenario_id=follow_up.scenario_id,
                    turn_id=third_turn.turn_id,
                    prompt=third_turn.prompt,
                ),
                workflow_response=workflow_response(source_package("three")),
            )
            record_cache.finalize()
            complete = json.loads(
                cache_path.read_text(encoding="utf-8")
            )
            self.assertTrue(complete["complete"])

    def test_cache_settings_require_an_explicit_non_live_path(self) -> None:
        config = benchmark_config()

        with self.assertRaisesRegex(
            EvidenceCacheError,
            "required in replay mode",
        ):
            benchmark_runner.build_evidence_cache(
                mode="replay",
                path=None,
                config=config,
            )

        with self.assertRaisesRegex(
            EvidenceCacheError,
            "requires --evidence-mode",
        ):
            benchmark_runner.build_evidence_cache(
                mode="live",
                path="unexpected.json",
                config=config,
            )

        parsed = benchmark_runner.build_argument_parser().parse_args(
            [
                "--model",
                "test-model",
                "--evidence-mode",
                "replay",
                "--evidence-cache",
                "frozen.json",
            ]
        )
        self.assertEqual(parsed.evidence_mode, "replay")
        self.assertEqual(parsed.evidence_cache, "frozen.json")


if __name__ == "__main__":
    unittest.main()
