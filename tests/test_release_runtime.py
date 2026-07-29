from __future__ import annotations

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import ollama_runtime
import visa_cli


class FakeHttpResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ollama_runtime.requests.HTTPError(
                f"HTTP {self.status_code}"
            )

    def json(self):
        return self.payload


class OllamaRuntimeTests(unittest.TestCase):
    @patch.object(ollama_runtime.requests, "request")
    def test_preflight_records_digest_capabilities_and_server(
        self,
        request: Mock,
    ) -> None:
        request.side_effect = [
            FakeHttpResponse(
                {
                    "models": [
                        {
                            "name": "qwen:test",
                            "digest": "sha256:model",
                        }
                    ]
                }
            ),
            FakeHttpResponse(
                {
                    "capabilities": [
                        "completion",
                        "tools",
                    ]
                }
            ),
            FakeHttpResponse({"version": "0.12.0"}),
        ]

        metadata = ollama_runtime.preflight_ollama_model(
            "qwen:test"
        )

        self.assertEqual(metadata.digest, "sha256:model")
        self.assertIn("tools", metadata.capabilities)
        self.assertEqual(metadata.server_version, "0.12.0")

    @patch.object(ollama_runtime.requests, "request")
    def test_preflight_rejects_model_without_tools(
        self,
        request: Mock,
    ) -> None:
        request.side_effect = [
            FakeHttpResponse(
                {
                    "models": [
                        {
                            "name": "text-only",
                            "digest": "digest",
                        }
                    ]
                }
            ),
            FakeHttpResponse({"capabilities": ["completion"]}),
            FakeHttpResponse({"version": "0.12.0"}),
        ]

        with self.assertRaisesRegex(
            ollama_runtime.OllamaPreflightError,
            "tool calling",
        ):
            ollama_runtime.preflight_ollama_model("text-only")

    @patch.object(ollama_runtime.requests, "request")
    def test_preflight_fails_closed_when_capabilities_are_missing(
        self,
        request: Mock,
    ) -> None:
        request.side_effect = [
            FakeHttpResponse(
                {
                    "models": [
                        {
                            "name": "unknown-capabilities",
                            "digest": "digest",
                        }
                    ]
                }
            ),
            FakeHttpResponse({}),
            FakeHttpResponse({"version": "0.12.0"}),
        ]

        with self.assertRaisesRegex(
            ollama_runtime.OllamaPreflightError,
            "нельзя подтвердить",
        ):
            ollama_runtime.preflight_ollama_model(
                "unknown-capabilities"
            )


class VisaCliReleaseTests(unittest.TestCase):
    def test_help_is_available_without_ollama(self) -> None:
        help_text = visa_cli.build_argument_parser().format_help()

        self.assertIn("--model", help_text)
        self.assertIn("--debug", help_text)
        self.assertIn("--workflow-db", help_text)

    def test_session_id_is_namespaced_per_experiment_run(self) -> None:
        first = visa_cli.build_cli_session_id(
            namespace="process-abc",
            experiment_run_id=1,
        )
        second = visa_cli.build_cli_session_id(
            namespace="process-abc",
            experiment_run_id=2,
        )

        self.assertNotEqual(first, second)
        self.assertIn("run-1", first)
        self.assertIn("run-2", second)

    def test_workflow_failure_is_persisted(self) -> None:
        reporter = Mock()
        reporter.start_run.return_value = 7
        reporter.get_run_number.return_value = 1
        workflow = Mock()
        workflow.run.side_effect = RuntimeError("workflow failed")
        metadata = ollama_runtime.OllamaModelMetadata(
            name="qwen:test",
            digest="digest",
            capabilities=("tools",),
            server_version="test",
        )

        with (
            patch.object(
                visa_cli,
                "preflight_ollama_model",
                return_value=metadata,
            ),
            patch.object(
                visa_cli,
                "ExperimentReporter",
                return_value=reporter,
            ),
            patch.object(
                visa_cli,
                "init_visa_workflow",
                return_value=workflow,
            ),
            patch(
                "builtins.input",
                side_effect=["Question?", "/exit"],
            ),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            exit_code = visa_cli.main(["--model", "qwen:test"])

        self.assertEqual(exit_code, 0)
        reporter.record_failure.assert_called_once()
        failure_kwargs = reporter.record_failure.call_args.kwargs
        self.assertEqual(failure_kwargs["question"], "Question?")
        self.assertIn("visa-cli-", failure_kwargs["session_id"])
        reporter.finish_run.assert_called_once_with(7)

    def test_eof_finishes_run_instead_of_looping(self) -> None:
        reporter = Mock()
        reporter.start_run.return_value = 8
        reporter.get_run_number.return_value = 2
        metadata = ollama_runtime.OllamaModelMetadata(
            name="qwen:test",
            digest="digest",
            capabilities=("tools",),
            server_version="test",
        )

        with (
            patch.object(
                visa_cli,
                "preflight_ollama_model",
                return_value=metadata,
            ),
            patch.object(
                visa_cli,
                "ExperimentReporter",
                return_value=reporter,
            ),
            patch.object(
                visa_cli,
                "init_visa_workflow",
                return_value=Mock(),
            ),
            patch("builtins.input", side_effect=EOFError),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            exit_code = visa_cli.main(["--model", "qwen:test"])

        self.assertEqual(exit_code, 0)
        reporter.finish_run.assert_called_once_with(8)


if __name__ == "__main__":
    unittest.main()
