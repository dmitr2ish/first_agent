from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup
from ddgs import DDGS
from pypdf import PdfReader

from agno.tools import tool


ALLOWED_DOMAINS = (
    "uscis.gov",
    "travel.state.gov",
    "state.gov",
    "dhs.gov",
    "cbp.gov",
    "usembassy.gov",
)

DEFAULT_MAX_PAGES = 5
MAX_DOWNLOAD_BYTES = 8_000_000
MAX_TEXT_CHARS_PER_SOURCE = 7_000
MAX_QUERY_CHARS = 500
MAX_REDIRECTS = 5

HTML_CONTENT_TYPES = {
    "",
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; VisaResearchAssistant/1.0; "
        "+local-research-tool)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/pdf,"
        "text/plain;q=0.9,*/*;q=0.8"
    ),
}


def _is_allowed_url(url: str) -> bool:
    """Проверяет протокол и домен URL."""

    if not isinstance(url, str) or not url:
        return False

    normalized_url = unicodedata.normalize("NFKC", url)

    # Do not silently reinterpret visually confusable or control-bearing
    # URLs. The exact value is later emitted in the trusted source appendix.
    if normalized_url != url:
        return False

    if (
        "\\" in url
        or any(
            character.isspace()
            or unicodedata.category(character) in {"Cc", "Cf"}
            for character in url
        )
        or any(character in url for character in '<>"`')
    ):
        return False

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme.casefold() != "https":
        return False

    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        return False

    hostname = (parsed.hostname or "").lower().rstrip(".")

    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ALLOWED_DOMAINS
    )


def _normalize_text(text: str) -> str:
    """Удаляет лишние пробелы и слишком большое количество переносов."""

    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _extract_html(raw_content: bytes, encoding: str | None) -> str:
    """Извлекает основной текст HTML."""

    html = raw_content.decode(encoding or "utf-8", errors="replace")

    # Основной extractor
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        output_format="txt",
    )

    if extracted:
        return _normalize_text(extracted)

    # Резервный вариант
    soup = BeautifulSoup(html, "html.parser")

    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form",
        ]
    ):
        element.decompose()

    return _normalize_text(soup.get_text("\n"))


def _extract_pdf(raw_content: bytes) -> str:
    """Извлекает текст из первых страниц PDF."""

    reader = PdfReader(io.BytesIO(raw_content))
    pages: list[str] = []

    # Ограничиваем число страниц, чтобы не переполнить контекст
    for page in reader.pages[:20]:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)

    return _normalize_text("\n\n".join(pages))


def _download_document(url: str) -> dict[str, Any]:
    """Загружает HTML или PDF и извлекает текст."""

    if not _is_allowed_url(url):
        raise ValueError(f"Domain is not allowed: {url}")

    current_url = url

    for redirect_count in range(MAX_REDIRECTS + 1):
        with requests.get(
            current_url,
            headers=REQUEST_HEADERS,
            timeout=(7, 25),
            allow_redirects=False,
            stream=True,
        ) as response:
            status_code = int(getattr(response, "status_code", 200))

            if status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError(
                        "Redirect response did not include Location."
                    )
                if redirect_count >= MAX_REDIRECTS:
                    raise ValueError("Too many redirects.")

                next_url = urldefrag(
                    urljoin(
                        str(getattr(response, "url", current_url)),
                        location,
                    )
                ).url
                if not _is_allowed_url(next_url):
                    raise ValueError(
                        "Redirected to a non-allowed domain: "
                        f"{next_url}"
                    )

                current_url = next_url
                continue

            response.raise_for_status()
            final_url = urldefrag(
                str(getattr(response, "url", current_url))
            ).url

            # A custom transport/mock can still report a different final URL.
            if not _is_allowed_url(final_url):
                raise ValueError(
                    f"Redirected to a non-allowed domain: {final_url}"
                )

            content_length = response.headers.get("Content-Length")
            if (
                content_length
                and int(content_length) > MAX_DOWNLOAD_BYTES
            ):
                raise ValueError(
                    f"Document is too large: {content_length} bytes"
                )

            chunks: list[bytes] = []
            downloaded_size = 0

            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue

                downloaded_size += len(chunk)

                if downloaded_size > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Document exceeded {MAX_DOWNLOAD_BYTES} bytes"
                    )

                chunks.append(chunk)

            raw_content = b"".join(chunks)
            content_type = (
                response.headers.get("Content-Type", "")
                .split(";")[0]
                .strip()
                .lower()
            )

            is_pdf = (
                content_type == "application/pdf"
                or final_url.lower().endswith(".pdf")
            )

            if is_pdf:
                text = _extract_pdf(raw_content)
                document_type = "pdf"
            else:
                if content_type not in HTML_CONTENT_TYPES:
                    raise ValueError(
                        "Unsupported document content type: "
                        f"{content_type or '<missing>'}"
                    )

                text = _extract_html(raw_content, response.encoding)
                document_type = "html"

            if not text:
                raise ValueError("No readable text was extracted")

            extracted_text = text[:MAX_TEXT_CHARS_PER_SOURCE]

            return {
                "final_url": final_url,
                "document_type": document_type,
                "content_type": content_type,
                "text": extracted_text,
                # content_sha256 binds the exact text consumed by the agents.
                "content_sha256": hashlib.sha256(
                    extracted_text.encode("utf-8")
                ).hexdigest(),
                "raw_content_sha256": hashlib.sha256(
                    raw_content
                ).hexdigest(),
            }

    raise ValueError("Too many redirects.")


def _search_candidates(query: str) -> list[dict[str, str]]:
    """Выполняет несколько поисков и объединяет результаты."""

    search_queries = [
        f"{query} site:travel.state.gov",
        f"{query} site:uscis.gov",
        (
            f"{query} "
            "(site:state.gov OR site:dhs.gov OR "
            "site:cbp.gov OR site:usembassy.gov)"
        ),
    ]

    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    search_errors: list[str] = []

    with DDGS() as ddgs:
        for search_query in search_queries:
            try:
                results = ddgs.text(search_query, max_results=8)

                for result in results:
                    if not isinstance(result, dict):
                        continue

                    url = (
                        result.get("href")
                        or result.get("url")
                        or ""
                    )

                    normalized_url = urldefrag(url).url

                    if not normalized_url:
                        continue

                    if not _is_allowed_url(normalized_url):
                        continue

                    if normalized_url in seen_urls:
                        continue

                    seen_urls.add(normalized_url)

                    candidates.append(
                        {
                            "title": str(
                                result.get("title") or ""
                            ).strip(),
                            "url": normalized_url,
                            "snippet": str(
                                result.get("body")
                                or result.get("snippet")
                                or ""
                            ).strip(),
                        }
                    )
            except Exception as error:
                search_errors.append(
                    f"{search_query}: {error}"
                )

    if not candidates and search_errors:
        raise RuntimeError(
            "All official-source searches failed: "
            + " | ".join(search_errors)
        )

    return candidates


def collect_official_sources_data(
    query: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """
    Ищет официальные американские источники и загружает их текст.

    Эта функция вызывается напрямую из Python, поэтому LLM не может
    решить «не использовать инструмент».
    """

    clean_query = " ".join(query.split())

    if not clean_query:
        raise ValueError("Query cannot be empty")

    if len(clean_query) > MAX_QUERY_CHARS:
        raise ValueError(
            f"Query exceeds {MAX_QUERY_CHARS} characters"
        )

    max_pages = max(1, min(max_pages, 5))

    candidates = _search_candidates(clean_query)

    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    # Пробуем больше кандидатов, поскольку часть страниц может
    # блокировать загрузку или не содержать извлекаемого текста.
    for candidate in candidates[:15]:
        if len(sources) >= max_pages:
            break

        try:
            document = _download_document(candidate["url"])

            source_id = f"S{len(sources) + 1}"

            sources.append(
                {
                    "source_id": source_id,
                    "title": candidate["title"],
                    "url": document["final_url"],
                    "search_snippet": candidate["snippet"],
                    "document_type": document["document_type"],
                    "content_type": document["content_type"],
                    "content_sha256": document["content_sha256"],
                    "raw_content_sha256": document[
                        "raw_content_sha256"
                    ],
                    "content": document["text"],
                }
            )

        except Exception as error:
            errors.append(
                {
                    "url": candidate["url"],
                    "error": str(error),
                }
            )

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
        "query": clean_query,
        "collected_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "allowed_domains": list(ALLOWED_DOMAINS),
        "source_count": len(sources),
        "evidence_package_digest": evidence_package_digest,
        "sources": sources,
        "errors": errors,
    }


@tool(
    name="collect_official_us_sources",
    description=(
        "Search official US government immigration websites, "
        "download 3-5 relevant pages, extract their main text, "
        "and return a JSON evidence package."
    ),
    cache_results=True,
    cache_ttl=3600,
    show_result=False,
)
def collect_official_us_sources(
    query: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> str:
    """
    Search and read official US government immigration sources.

    Args:
        query: Research query in English.
        max_pages: Number of successfully downloaded pages, from 1 to 5.

    Returns:
        JSON containing source IDs, URLs and extracted page contents.
    """

    result = collect_official_sources_data(
        query=query,
        max_pages=max_pages,
    )

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
