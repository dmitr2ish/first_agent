import json
import io
import unittest
from contextlib import redirect_stdout

from agno.run import RunContext

from source_analysis_tools import (
    list_official_sources,
    read_official_source,
    search_official_sources,
    trace_tool_call,
)


def make_context(source_package: object) -> RunContext:
    return RunContext(
        run_id="test-run",
        session_id="test-session",
        dependencies={"source_package": source_package},
    )


class SourceAnalysisToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_package = {
            "query": "O-1 visa evidence",
            "sources": [
                {
                    "source_id": "S1",
                    "title": "O-1 Visa",
                    "url": "https://www.uscis.gov/example",
                    "document_type": "official_page",
                    "content": "prefix " + "extraordinary ability " * 80,
                },
                {
                    "source_id": "S2",
                    "title": "Second source",
                    "url": "https://travel.state.gov/example",
                    "document_type": "official_page",
                    "content": "temporary worker classification",
                },
            ],
        }
        self.context = make_context(self.source_package)

    def test_list_sources_uses_run_context_dependencies(self) -> None:
        result = json.loads(list_official_sources(self.context))

        self.assertEqual([item["source_id"] for item in result], ["S1", "S2"])
        self.assertNotIn("content", result[0])

    def test_read_source_and_unknown_source_id(self) -> None:
        result = json.loads(
            read_official_source(
                "s1",
                start_char=7,
                max_chars=600,
                run_context=self.context,
            )
        )
        missing = json.loads(
            read_official_source("S99", run_context=self.context)
        )

        self.assertEqual(result["source_id"], "S1")
        self.assertEqual(result["start_char"], 7)
        self.assertEqual(result["returned_chars"], 600)
        self.assertIn("error", missing)
        self.assertEqual(missing["available_source_ids"], ["S1", "S2"])

    def test_read_source_clamps_offsets_and_length(self) -> None:
        lower_bound = json.loads(
            read_official_source(
                "S1",
                start_char=-50,
                max_chars=1,
                run_context=self.context,
            )
        )
        upper_bound = json.loads(
            read_official_source(
                "S1",
                max_chars=50_000,
                run_context=self.context,
            )
        )

        self.assertEqual(lower_bound["start_char"], 0)
        self.assertEqual(lower_bound["returned_chars"], 500)
        self.assertLessEqual(upper_bound["returned_chars"], 8_000)

    def test_search_sources_and_max_results_limit(self) -> None:
        result = json.loads(
            search_official_sources(
                "classification extraordinary",
                max_results=1,
                run_context=self.context,
            )
        )

        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["source_id"], "S1")

    def test_invalid_source_package_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing or invalid"):
            list_official_sources(make_context(None))

    def test_trace_hook_prints_name_arguments_result_and_time(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            result = trace_tool_call(
                function_name="sample_tool",
                function_call=lambda value: f"result:{value}",
                arguments={"value": "evidence"},
                run_context=self.context,
            )

        trace = stdout.getvalue()
        self.assertEqual(result, "result:evidence")
        self.assertIn("sample_tool", trace)
        self.assertIn('"value": "evidence"', trace)
        self.assertIn("result:evidence", trace)
        self.assertIn("TOOL TIME", trace)


if __name__ == "__main__":
    unittest.main()
