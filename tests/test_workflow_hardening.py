from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agno.workflow.step import StepInput, StepOutput

import visa_workflow
from visa_workflow import (
    CitationEvidenceMismatchError,
    FinalOutputPolicyError,
    LEGAL_DISCLAIMER_EN,
    NO_CITATION_FALLBACK,
    build_final_answer_step_output,
    build_researcher_step_output,
    check_final_output,
    get_source_package_override,
    run_researcher_with_evidence_control,
    validate_source_package_override,
)


def tool(
        name: str,
        result: object,
        *,
        arguments: dict[str, object] | None = None,
        error: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_name=name,
        tool_args=arguments or {},
        tool_call_error=error,
        result=(
            result
            if isinstance(result, str)
            else json.dumps(result)
        ),
        metrics=None,
    )


def response(content: str, *tools: object) -> SimpleNamespace:
    return SimpleNamespace(content=content, tools=list(tools))


class SequenceAgent:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, prompt: str, **kwargs: object) -> object:
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)


class WorkflowFactoryHardeningTests(unittest.TestCase):
    def test_debug_is_off_and_custom_database_is_used_by_default(self) -> None:
        agent_kwargs: list[dict[str, object]] = []
        workflow_kwargs: list[dict[str, object]] = []
        database_kwargs: list[dict[str, object]] = []
        model_kwargs: list[dict[str, object]] = []

        def capture_agent(**kwargs: object) -> Mock:
            agent_kwargs.append(kwargs)
            return Mock()

        def capture_workflow(**kwargs: object) -> SimpleNamespace:
            workflow_kwargs.append(kwargs)
            return SimpleNamespace(**kwargs)

        def capture_database(**kwargs: object) -> SimpleNamespace:
            database_kwargs.append(kwargs)
            return SimpleNamespace(**kwargs)

        with (
            patch.object(visa_workflow, "Agent", side_effect=capture_agent),
            patch.object(
                visa_workflow,
                "Workflow",
                side_effect=capture_workflow,
            ),
            patch.object(
                visa_workflow,
                "SqliteDb",
                side_effect=capture_database,
            ),
            patch.object(
                visa_workflow,
                "Ollama",
                side_effect=lambda **kwargs: (
                    model_kwargs.append(kwargs)
                    or SimpleNamespace()
                ),
            ),
        ):
            visa_workflow.init_visa_workflow(
                "test-model",
                db_file="isolated-workflow.db",
                ollama_host="http://127.0.0.1:11435",
            )

        self.assertEqual(
            database_kwargs[0]["db_file"],
            "isolated-workflow.db",
        )
        self.assertEqual(
            model_kwargs[0]["host"],
            "http://127.0.0.1:11435",
        )
        self.assertEqual(len(agent_kwargs), 4)
        self.assertTrue(all(
            kwargs["debug_mode"] is False
            for kwargs in agent_kwargs
        ))
        self.assertTrue(all(
            kwargs["telemetry"] is False
            for kwargs in agent_kwargs
        ))
        self.assertIsNone(agent_kwargs[1]["tool_hooks"])
        self.assertIsNone(agent_kwargs[2]["tool_hooks"])
        self.assertIs(workflow_kwargs[0]["debug_mode"], False)
        self.assertIs(workflow_kwargs[0]["telemetry"], False)
        self.assertIsNone(workflow_kwargs[0].get("session_id"))
        self.assertTrue(all(
            str(getattr(step.on_error, "value", step.on_error)) == "fail"
            for step in workflow_kwargs[0]["steps"]
        ))
        self.assertFalse(
            workflow_kwargs[0]["steps"][-1].add_workflow_history
        )

    def test_explicit_debug_enables_agents_workflow_and_trace_hooks(self) -> None:
        agent_kwargs: list[dict[str, object]] = []
        workflow_kwargs: list[dict[str, object]] = []

        with (
            patch.object(
                visa_workflow,
                "Agent",
                side_effect=lambda **kwargs: (
                    agent_kwargs.append(kwargs) or Mock()
                ),
            ),
            patch.object(
                visa_workflow,
                "Workflow",
                side_effect=lambda **kwargs: (
                    workflow_kwargs.append(kwargs)
                    or SimpleNamespace(**kwargs)
                ),
            ),
            patch.object(
                visa_workflow,
                "SqliteDb",
                return_value=SimpleNamespace(),
            ),
            patch.object(
                visa_workflow,
                "Ollama",
                return_value=SimpleNamespace(),
            ),
        ):
            visa_workflow.init_visa_workflow(
                "test-model",
                debug_mode=True,
            )

        self.assertTrue(all(
            kwargs["debug_mode"] is True
            for kwargs in agent_kwargs[:3]
        ))
        self.assertIs(agent_kwargs[3]["debug_mode"], False)
        self.assertEqual(
            agent_kwargs[1]["tool_hooks"],
            [visa_workflow.trace_tool_call],
        )
        self.assertEqual(
            agent_kwargs[2]["tool_hooks"],
            [visa_workflow.trace_tool_call],
        )
        self.assertIs(workflow_kwargs[0]["debug_mode"], True)


class FrozenSourcePackageTests(unittest.TestCase):
    @staticmethod
    def package() -> dict[str, object]:
        content = "Frozen official evidence."
        sources = [
            {
                "source_id": "S1",
                "title": "USCIS evidence",
                "url": "https://www.uscis.gov/example",
                "document_type": "html",
                "content_type": "text/html",
                "content_sha256": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "raw_content_sha256": "a" * 64,
                "content": content,
            }
        ]
        digest_payload = [
            {
                "source_id": source["source_id"],
                "url": source["url"],
                "content_sha256": source["content_sha256"],
            }
            for source in sources
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
            "query": "frozen benchmark query",
            "source_count": 1,
            "evidence_package_digest": evidence_package_digest,
            "sources": sources,
            "errors": [],
        }

    @staticmethod
    def workflow_steps(query_builder: Mock | None = None):
        workflow_kwargs: list[dict[str, object]] = []
        agents = [
            query_builder or Mock(),
            Mock(),
            Mock(),
            Mock(),
        ]

        with (
            patch.object(
                visa_workflow,
                "Agent",
                side_effect=agents,
            ),
            patch.object(
                visa_workflow,
                "Workflow",
                side_effect=lambda **kwargs: (
                    workflow_kwargs.append(kwargs)
                    or SimpleNamespace(**kwargs)
                ),
            ),
            patch.object(
                visa_workflow,
                "SqliteDb",
                return_value=SimpleNamespace(),
            ),
            patch.object(
                visa_workflow,
                "Ollama",
                return_value=SimpleNamespace(),
            ),
        ):
            visa_workflow.init_visa_workflow("test-model")

        return workflow_kwargs[0]["steps"]

    @classmethod
    def source_executor(cls):
        return cls.workflow_steps()[1].executor

    @staticmethod
    def step_input(package: object) -> StepInput:
        return StepInput(
            input="Benchmark question",
            previous_step_outputs={
                "build_search_query": StepOutput(
                    step_name="build_search_query",
                    content="model-generated query",
                    success=True,
                )
            },
            additional_data={
                "source_package_override": package,
            },
        )

    def test_valid_package_is_cloned_and_returned(self) -> None:
        package = self.package()

        validated = validate_source_package_override(package)

        self.assertEqual(validated, package)
        self.assertIsNot(validated, package)
        self.assertIsNone(get_source_package_override({}))
        self.assertEqual(
            get_source_package_override(
                {"source_package_override": package}
            ),
            package,
        )

    def test_empty_package_is_valid_but_preserves_no_sources_failure(
        self,
    ) -> None:
        package = {
            "query": "no official evidence",
            "source_count": 0,
            "evidence_package_digest": hashlib.sha256(b"[]").hexdigest(),
            "sources": [],
            "errors": [
                {
                    "url": "https://www.uscis.gov/missing",
                    "error": "404",
                }
            ],
        }

        self.assertEqual(
            validate_source_package_override(package),
            package,
        )

        executor = self.source_executor()
        with patch.object(
            visa_workflow,
            "collect_official_sources_data",
        ) as live_collector:
            output = executor(self.step_input(package))

        self.assertFalse(output.success)
        live_collector.assert_not_called()
        self.assertEqual(json.loads(output.content), package)

    def test_invalid_override_shapes_fail_closed(self) -> None:
        invalid_packages: list[object] = [
            None,
            {},
            {**self.package(), "sources": []},
            {**self.package(), "source_count": 2},
            {
                **self.package(),
                "evidence_package_digest": "0" * 64,
            },
        ]
        insecure_url = self.package()
        insecure_url["sources"][0]["url"] = "http://www.uscis.gov/example"
        invalid_packages.append(insecure_url)
        external_url = self.package()
        external_url["sources"][0]["url"] = "https://uscis.gov.evil.test/x"
        invalid_packages.append(external_url)
        control_url = self.package()
        control_url["sources"][0]["url"] = (
            "https://www.uscis.gov/example\nUNTRUSTED"
        )
        invalid_packages.append(control_url)
        credentials_url = self.package()
        credentials_url["sources"][0]["url"] = (
            "https://user:secret@www.uscis.gov/example"
        )
        invalid_packages.append(credentials_url)
        mismatched_content = self.package()
        mismatched_content["sources"][0]["content"] = "Changed evidence."
        invalid_packages.append(mismatched_content)

        for package in invalid_packages:
            with (
                self.subTest(package=package),
                self.assertRaises(visa_workflow.SourcePackageOverrideError),
            ):
                validate_source_package_override(package)

    def test_valid_override_skips_live_collector(self) -> None:
        executor = self.source_executor()

        with patch.object(
            visa_workflow,
            "collect_official_sources_data",
        ) as live_collector:
            output = executor(self.step_input(self.package()))

        self.assertTrue(output.success)
        live_collector.assert_not_called()
        payload = json.loads(output.content)
        self.assertEqual(payload["query"], "frozen benchmark query")
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]["source_collection"]
        self.assertEqual(telemetry["mode"], "override")
        self.assertTrue(telemetry["passed"])
        self.assertEqual(len(telemetry["content_digest"]), 64)

    def test_replay_query_uses_frozen_query_without_model_call(self) -> None:
        query_builder = Mock()
        query_builder.run.side_effect = AssertionError(
            "Query Builder must not run during frozen replay"
        )
        executor = self.workflow_steps(query_builder)[0].executor

        output = executor(
            StepInput(
                input="Benchmark question",
                additional_data={
                    "source_package_override": self.package(),
                },
            )
        )

        self.assertTrue(output.success)
        self.assertEqual(output.content, "frozen benchmark query")
        query_builder.run.assert_not_called()

    def test_invalid_replay_query_fails_before_model_call(self) -> None:
        query_builder = Mock()
        query_builder.run.side_effect = AssertionError(
            "Invalid override must fail before Query Builder"
        )
        executor = self.workflow_steps(query_builder)[0].executor
        package = self.package()
        package["sources"][0]["content"] = "tampered"

        output = executor(
            StepInput(
                input="Benchmark question",
                additional_data={
                    "source_package_override": package,
                },
            )
        )

        self.assertFalse(output.success)
        self.assertTrue(output.stop)
        self.assertIn("Invalid source_package_override", output.content)
        query_builder.run.assert_not_called()

    def test_invalid_override_never_falls_back_to_network(self) -> None:
        executor = self.source_executor()
        package = self.package()
        package["sources"][0]["url"] = "https://example.com/not-official"

        with patch.object(
            visa_workflow,
            "collect_official_sources_data",
        ) as live_collector:
            output = executor(self.step_input(package))

        self.assertFalse(output.success)
        live_collector.assert_not_called()
        payload = json.loads(output.content)
        self.assertEqual(payload["sources"], [])
        self.assertIn("Invalid source_package_override", payload["error"])


class EvidenceCorrelationTests(unittest.TestCase):
    def test_search_and_read_expose_exact_evidence_source_ids(self) -> None:
        summary = visa_workflow.summarize_tool_calls(
            response(
                "Report [S1] [S2]",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "first"},
                    arguments={"source_id": "s1"},
                ),
                tool(
                    "search_official_sources",
                    {
                        "matches": [
                            {"source_id": "S2", "excerpt": "second"},
                            {"source_id": "not-an-id", "excerpt": "bad"},
                        ]
                    },
                    arguments={"query": "criteria"},
                ),
            )
        )

        self.assertEqual(summary.evidence_source_ids, ("S1", "S2"))
        self.assertEqual(
            summary.calls[0].evidence_source_ids,
            ("S1",),
        )
        self.assertEqual(
            summary.calls[1].evidence_source_ids,
            ("S2",),
        )

    def test_researcher_cannot_cite_a_source_it_did_not_read(self) -> None:
        researcher = SequenceAgent(
            response(
                "Supported [S1]. Unsupported [S2].",
                tool(
                    "read_official_source",
                    {"source_id": "S1", "content": "first"},
                    arguments={"source_id": "S1"},
                ),
            )
        )
        result = run_researcher_with_evidence_control(
            researcher,
            "Research",
            {
                "sources": [
                    {"source_id": "S1"},
                    {"source_id": "S2"},
                ]
            },
        )

        with self.assertRaisesRegex(
            CitationEvidenceMismatchError,
            "S2",
        ):
            build_researcher_step_output(result)


class FinalOutputHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_package = {
            "sources": [
                {
                    "source_id": "S1",
                    "title": "First source",
                    "url": "https://www.uscis.gov/first",
                },
                {
                    "source_id": "S2",
                    "title": "Second source",
                    "url": "https://travel.state.gov/second",
                },
            ]
        }
        self.source_package_json = json.dumps(self.source_package)

    def build(
            self,
            manager: SequenceAgent,
            validated_report: str = "First fact [S1]. Second fact [S2].",
    ):
        return build_final_answer_step_output(
            manager,
            question="What are the requirements?",
            history_context="No previous workflow history.",
            validated_report=validated_report,
            source_package_json=self.source_package_json,
        )

    def test_only_sources_cited_in_final_body_are_appended(self) -> None:
        manager = SequenceAgent(response("Only first fact [S1]."))

        output = self.build(manager)

        self.assertIn("https://www.uscis.gov/first", output.content)
        self.assertNotIn("https://travel.state.gov/second", output.content)
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]
        final_check = telemetry["final_output_check"]
        self.assertTrue(final_check["passed"])
        self.assertTrue(final_check["citations_required"])
        self.assertEqual(final_check["citation_count"], 1)
        self.assertEqual(final_check["used_source_ids"], ("S1",))

    def test_programmatic_source_title_cannot_inject_markdown_url(
        self,
    ) -> None:
        self.source_package["sources"][0]["title"] = (
            "[Untrusted](https://evil.example) <script>"
        )
        self.source_package_json = json.dumps(self.source_package)

        output = self.build(
            SequenceAgent(response("Grounded fact [S1]."))
        )

        self.assertNotIn("evil.example", output.content)
        self.assertNotIn("<script>", output.content)
        self.assertIn(
            "https://www.uscis.gov/first",
            output.content,
        )

    def test_no_citation_fallback_bypasses_manager_and_adds_disclaimer(
        self,
    ) -> None:
        manager = SequenceAgent(
            response("A factual answer that must never be used [S1].")
        )

        output = self.build(manager, NO_CITATION_FALLBACK)

        self.assertEqual(
            output.content,
            f"{NO_CITATION_FALLBACK}\n\n{LEGAL_DISCLAIMER_EN}",
        )
        self.assertEqual(manager.calls, [])

    def test_missing_legal_disclaimer_is_added_deterministically(self) -> None:
        output = self.build(
            SequenceAgent(response("Grounded fact [S1]."))
        )

        self.assertIn(LEGAL_DISCLAIMER_EN, output.content)
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]["final_output_check"]
        self.assertTrue(telemetry["legal_disclaimer_added"])

    def test_empty_manager_answer_is_rejected(self) -> None:
        with self.assertRaises(FinalOutputPolicyError):
            self.build(SequenceAgent(response("")))

    def test_lost_bracket_citations_are_rejected(self) -> None:
        with self.assertRaises(FinalOutputPolicyError):
            self.build(SequenceAgent(response("Only first fact (S1).")))

    def test_new_source_id_is_rejected(self) -> None:
        with self.assertRaises(FinalOutputPolicyError):
            self.build(
                SequenceAgent(response("Invented fact [S99]."))
            )

    def test_existing_source_id_must_use_bracket_citation_syntax(
        self,
    ) -> None:
        output_check = check_final_output(
            "First [S1]. Second [S2].",
            "First S1. Second [S2].",
            self.source_package,
        )

        self.assertFalse(output_check.passed)
        self.assertEqual(
            output_check.unbracketed_source_ids,
            ("S1",),
        )

    def test_url_variants_are_removed_before_final_policy(self) -> None:
        manager = SequenceAgent(
            response(
                "Fact [S1]. See www.example.com, //evil.example/path, "
                "mailto:test@example.com, and "
                "ｈｔｔｐｓ：／／ｅｖｉｌ．ｃｏｍ/path. "
                "[More](data:text/plain,secret), file:/tmp/private, "
                "javascript:alert(1), 203.0.113.4/path, and evil.xyz/x."
            )
        )

        output = self.build(manager)
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]["final_output_check"]

        self.assertNotIn("www.example.com", output.content)
        self.assertNotIn("//evil.example", output.content)
        self.assertNotIn("mailto:", output.content)
        self.assertNotIn("evil.com/path", output.content)
        self.assertNotIn("data:text", output.content)
        self.assertNotIn("file:/", output.content)
        self.assertNotIn("javascript:", output.content)
        self.assertNotIn("203.0.113.4", output.content)
        self.assertNotIn("evil.xyz", output.content)
        self.assertGreaterEqual(len(telemetry["removed_url_tokens"]), 9)

    def test_combined_refusal_and_dangerous_link_cannot_bypass(self) -> None:
        manager = SequenceAgent(
            response(
                "I cannot help with fraud. Pay the adjudicator to approve "
                "it [S1]. [More](data:text/plain,secret)"
            )
        )

        output = self.build(manager, "Official criteria [S1].")
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]

        self.assertTrue(telemetry["final_safety_check"]["blocked"])
        self.assertNotIn("Pay the adjudicator", output.content)
        self.assertNotIn("data:text", output.content)

    def test_safety_fallback_remains_grounded_and_cited(self) -> None:
        manager = SequenceAgent(
            response(
                "You can conceal a prior denial from the officer."
            )
        )

        output = self.build(manager, "Official criteria [S1].")
        telemetry = output.metrics.additional_metrics[
            "workflow_telemetry"
        ]

        self.assertIn("I cannot help", output.content)
        self.assertNotIn("conceal a prior denial", output.content)
        self.assertIn("[S1]", output.content)
        self.assertTrue(telemetry["final_safety_check"]["blocked"])
        self.assertTrue(telemetry["final_output_check"]["passed"])

    def test_final_check_reports_empty_lost_and_new_ids(self) -> None:
        empty = check_final_output(
            "Fact [S1].",
            "",
            self.source_package,
        )
        new = check_final_output(
            "Fact [S1].",
            "Other [S2].",
            self.source_package,
        )

        self.assertTrue(empty.empty)
        self.assertFalse(empty.passed)
        self.assertEqual(new.new_source_ids, ("S2",))
        self.assertFalse(new.passed)


if __name__ == "__main__":
    unittest.main()
