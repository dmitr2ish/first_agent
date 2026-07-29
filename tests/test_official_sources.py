from __future__ import annotations

import hashlib
import unittest
from unittest.mock import Mock, patch

from visa_research.sources import collector as official_sources


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        body: bytes = b"<html><body>Official evidence</body></html>",
        content_type: str = "text/html",
        content_length: str | None = None,
        status_code: int = 200,
        location: str | None = None,
    ) -> None:
        self.url = url
        self.body = body
        self.status_code = status_code
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        if location is not None:
            self.headers["Location"] = location

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self.body


class FakeDDGS:
    def __init__(self, responses):
        self.responses = iter(responses)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def text(self, query: str, max_results: int):
        del query, max_results
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class OfficialSourcesTests(unittest.TestCase):
    def test_url_policy_is_https_only_and_resists_suffix_spoofing(self) -> None:
        self.assertTrue(
            official_sources._is_allowed_url(
                "https://www.uscis.gov/example"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "http://www.uscis.gov/example"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "https://uscis.gov.attacker.example/page"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "file:///etc/passwd"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "https://www.uscis.gov/page\nINJECTED"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "https://user:secret@www.uscis.gov/page"
            )
        )
        self.assertFalse(
            official_sources._is_allowed_url(
                "https://www.uscis.gov:444/page"
            )
        )

    @patch.object(official_sources.requests, "get")
    def test_redirect_is_revalidated(self, request_get: Mock) -> None:
        request_get.return_value = FakeResponse(
            url="https://attacker.example/redirected",
        )

        with self.assertRaisesRegex(
            ValueError,
            "Redirected to a non-allowed domain",
        ):
            official_sources._download_document(
                "https://www.uscis.gov/start"
            )

    @patch.object(official_sources.requests, "get")
    def test_external_redirect_is_rejected_before_following(
        self,
        request_get: Mock,
    ) -> None:
        request_get.return_value = FakeResponse(
            url="https://www.uscis.gov/start",
            status_code=302,
            location="https://attacker.example/landing",
        )

        with self.assertRaisesRegex(
            ValueError,
            "Redirected to a non-allowed domain",
        ):
            official_sources._download_document(
                "https://www.uscis.gov/start"
            )

        request_get.assert_called_once()
        self.assertFalse(
            request_get.call_args.kwargs["allow_redirects"]
        )

    @patch.object(official_sources.requests, "get")
    def test_rejects_large_declared_document(self, request_get: Mock) -> None:
        request_get.return_value = FakeResponse(
            url="https://www.uscis.gov/large",
            content_length=str(
                official_sources.MAX_DOWNLOAD_BYTES + 1
            ),
        )

        with self.assertRaisesRegex(ValueError, "too large"):
            official_sources._download_document(
                "https://www.uscis.gov/large"
            )

    @patch.object(official_sources.requests, "get")
    def test_rejects_unsupported_content_type(
        self,
        request_get: Mock,
    ) -> None:
        request_get.return_value = FakeResponse(
            url="https://www.uscis.gov/image",
            body=b"\x89PNG",
            content_type="image/png",
        )

        with self.assertRaisesRegex(
            ValueError,
            "Unsupported document content type",
        ):
            official_sources._download_document(
                "https://www.uscis.gov/image"
            )

    @patch.object(official_sources.requests, "get")
    def test_html_download_records_content_hash(
        self,
        request_get: Mock,
    ) -> None:
        body = b"<html><body><main>Official visa rule</main></body></html>"
        request_get.return_value = FakeResponse(
            url="https://www.uscis.gov/rule",
            body=body,
        )

        result = official_sources._download_document(
            "https://www.uscis.gov/rule"
        )

        self.assertEqual(result["document_type"], "html")
        self.assertIn("Official visa rule", result["text"])
        self.assertEqual(
            result["content_sha256"],
            hashlib.sha256(
                result["text"].encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            result["raw_content_sha256"],
            hashlib.sha256(body).hexdigest(),
        )

    @patch.object(official_sources, "_extract_pdf")
    @patch.object(official_sources.requests, "get")
    def test_pdf_content_type_uses_pdf_extractor(
        self,
        request_get: Mock,
        extract_pdf: Mock,
    ) -> None:
        extract_pdf.return_value = "Extracted official PDF"
        request_get.return_value = FakeResponse(
            url="https://www.uscis.gov/form",
            body=b"%PDF-test",
            content_type="application/pdf",
        )

        result = official_sources._download_document(
            "https://www.uscis.gov/form"
        )

        self.assertEqual(result["document_type"], "pdf")
        self.assertEqual(result["text"], "Extracted official PDF")
        extract_pdf.assert_called_once_with(b"%PDF-test")

    def test_search_continues_after_one_provider_error_and_deduplicates(
        self,
    ) -> None:
        duplicate = {
            "title": "USCIS rule",
            "href": "https://www.uscis.gov/rule#section",
            "body": "snippet",
        }
        fake_ddgs = FakeDDGS(
            [
                RuntimeError("temporary search error"),
                [duplicate],
                [duplicate],
            ]
        )

        with patch.object(
            official_sources,
            "DDGS",
            return_value=fake_ddgs,
        ):
            candidates = official_sources._search_candidates("O-1")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["url"],
            "https://www.uscis.gov/rule",
        )

    def test_search_raises_when_every_provider_query_fails(self) -> None:
        fake_ddgs = FakeDDGS(
            [
                RuntimeError("one"),
                RuntimeError("two"),
                RuntimeError("three"),
            ]
        )

        with (
            patch.object(
                official_sources,
                "DDGS",
                return_value=fake_ddgs,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "All official-source searches failed",
            ),
        ):
            official_sources._search_candidates("O-1")

    def test_collector_limits_query_and_hashes_evidence_package(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds"):
            official_sources.collect_official_sources_data(
                "x" * (official_sources.MAX_QUERY_CHARS + 1)
            )

        candidates = [
            {
                "title": "Official",
                "url": "https://www.uscis.gov/rule",
                "snippet": "snippet",
            }
        ]
        document = {
            "final_url": "https://www.uscis.gov/rule",
            "document_type": "html",
            "content_type": "text/html",
            "content_sha256": "a" * 64,
            "raw_content_sha256": "b" * 64,
            "text": "Official evidence",
        }

        with (
            patch.object(
                official_sources,
                "_search_candidates",
                return_value=candidates,
            ),
            patch.object(
                official_sources,
                "_download_document",
                return_value=document,
            ),
        ):
            package = official_sources.collect_official_sources_data(
                "O-1 official criteria",
                max_pages=1,
            )

        self.assertEqual(package["source_count"], 1)
        self.assertEqual(len(package["evidence_package_digest"]), 64)
        self.assertEqual(
            package["sources"][0]["content_sha256"],
            "a" * 64,
        )


if __name__ == "__main__":
    unittest.main()
