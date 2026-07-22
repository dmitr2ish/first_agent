import unittest

from visa_workflow import run_researcher_with_sources


class RecordingResearcher:
    def __init__(self) -> None:
        self.calls = []

    def run(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return object()


class ResearcherDependenciesTests(unittest.TestCase):
    def test_source_package_is_passed_via_dependencies(self) -> None:
        researcher = RecordingResearcher()
        source_package = {
            "sources": [
                {
                    "source_id": "S1",
                    "content": "SECRET EVIDENCE TEXT",
                }
            ]
        }
        prompt = "Use the available source-analysis tools."

        response = run_researcher_with_sources(
            researcher,
            prompt,
            source_package,
        )

        self.assertIsNotNone(response)
        recorded_prompt, kwargs = researcher.calls[0]
        self.assertEqual(recorded_prompt, prompt)
        self.assertNotIn("SECRET EVIDENCE TEXT", recorded_prompt)
        self.assertFalse(kwargs["stream"])
        self.assertIs(
            kwargs["dependencies"]["source_package"],
            source_package,
        )


if __name__ == "__main__":
    unittest.main()
