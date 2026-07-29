from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from agno.run import RunContext


def _get_source_package(run_context: RunContext, ) -> dict[str, Any]:
    """
    Получает пакет источников, переданный через dependencies.
    """

    dependencies = run_context.dependencies or {}
    source_package = dependencies.get("source_package")

    if not isinstance(source_package, dict):
        raise ValueError(
            "The source_package dependency is missing or invalid."
        )

    sources = source_package.get("sources")

    if not isinstance(sources, list):
        raise ValueError(
            "The source_package does not contain a sources list."
        )

    return source_package


def list_official_sources(run_context: RunContext, ) -> str:
    """
    List all official sources available for the current research run.

    Returns source IDs, titles, URLs and document types.
    Use this tool before selecting sources to read.
    """

    source_package = _get_source_package(run_context)

    result: list[dict[str, Any]] = []

    for source in source_package["sources"]:
        result.append(
            {
                "source_id": source.get("source_id"),
                "title": source.get("title"),
                "url": source.get("url"),
                "document_type": source.get(
                    "document_type"
                ),
                "content_length": len(
                    source.get("content", "")
                ),
            }
        )

    return json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )


def read_official_source(
        source_id: str,
        start_char: int = 0,
        max_chars: int = 5000,
        run_context: RunContext | None = None,
) -> str:
    """
    Read a section of one official source.

    Args:
        source_id: Source identifier such as S1 or S2.
        start_char: Character offset at which reading begins.
        max_chars: Maximum number of characters to return.
    """

    if run_context is None:
        raise ValueError("RunContext was not provided.")

    source_package = _get_source_package(run_context)

    normalized_id = source_id.strip().upper()
    start_char = max(0, start_char)
    max_chars = max(500, min(max_chars, 8000))

    for source in source_package["sources"]:
        current_id = str(
            source.get("source_id", "")
        ).upper()

        if current_id != normalized_id:
            continue

        content = str(source.get("content", ""))

        selected_text = content[
            start_char:start_char + max_chars
        ]

        return json.dumps(
            {
                "source_id": source.get("source_id"),
                "title": source.get("title"),
                "url": source.get("url"),
                "start_char": start_char,
                "returned_chars": len(selected_text),
                "total_chars": len(content),
                "content": selected_text,
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {
            "error": f"Source {source_id} was not found.",
            "available_source_ids": [
                source.get("source_id")
                for source in source_package["sources"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def search_official_sources(
        query: str,
        max_results: int = 6,
        run_context: RunContext | None = None,
) -> str:
    """
    Search inside the text of downloaded official sources.

    Args:
        query: Words or phrase to find.
        max_results: Maximum number of matching excerpts.
    """

    if run_context is None:
        raise ValueError("RunContext was not provided.")

    source_package = _get_source_package(run_context)

    normalized_query = query.casefold().strip()

    query_terms = [
        term
        for term in re.split(
            r"[^\w\-]+",
            normalized_query,
        )
        if len(term) >= 2
    ]

    if not query_terms:
        return json.dumps(
            {"error": "Search query contains no useful terms."},
            ensure_ascii=False,
        )

    max_results = max(1, min(max_results, 10))
    matches: list[dict[str, Any]] = []

    for source in source_package["sources"]:
        content = str(source.get("content", ""))
        normalized_content = content.casefold()

        positions: list[int] = []
        score = 0

        for term in query_terms:
            position = normalized_content.find(term)

            if position >= 0:
                positions.append(position)
                score += normalized_content.count(term)

        if not positions:
            continue

        first_position = min(positions)
        excerpt_start = max(0, first_position - 350)
        excerpt_end = min(
            len(content),
            first_position + 1300,
        )

        matches.append(
            {
                "source_id": source.get("source_id"),
                "title": source.get("title"),
                "url": source.get("url"),
                "score": score,
                "excerpt_start": excerpt_start,
                "excerpt": content[
                    excerpt_start:excerpt_end
                ],
            }
        )

    matches.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return json.dumps(
        {
            "query": query,
            "matches": matches[:max_results],
        },
        ensure_ascii=False,
        indent=2,
    )


def check_source_ids(
        source_ids: list[str],
        run_context: RunContext,
) -> str:
    """
    Check whether source IDs used in a report really exist.
    """

    source_package = _get_source_package(run_context)

    available_ids = {
        str(source.get("source_id", "")).upper()
        for source in source_package["sources"]
    }

    requested_ids = {
        source_id.strip().upper()
        for source_id in source_ids
    }

    return json.dumps(
        {
            "valid": sorted(
                requested_ids & available_ids
            ),
            "invalid": sorted(
                requested_ids - available_ids
            ),
            "available": sorted(available_ids),
        },
        ensure_ascii=False,
        indent=2,
    )


def trace_tool_call(
        function_name: str,
        function_call: Callable[..., Any],
        arguments: dict[str, Any],
        run_context: RunContext,
) -> Any:
    """
    Показывает каждый tool call и время его исполнения.
    """

    started_at = time.perf_counter()

    printable_arguments: dict[str, Any] = {}

    for key, value in arguments.items():
        text = str(value)

        printable_arguments[key] = (
            text
            if len(text) <= 300
            else text[:300] + "...[truncated]"
        )

    print("\n" + "=" * 70)
    print(
        f"[🔧 TOOL CALL] Agent run: "
        f"{run_context.run_id}"
    )
    print(f"[🔧 TOOL CALL] Tool: {function_name}")
    print(
        "[🔧 TOOL CALL] Arguments:\n"
        + json.dumps(
            printable_arguments,
            ensure_ascii=False,
            indent=2,
        )
    )

    try:
        result = function_call(**arguments)
    except Exception as error:
        elapsed = time.perf_counter() - started_at

        print(
            f"[❌ TOOL ERROR] {function_name}: "
            f"{error}"
        )
        print(f"[⏱ TOOL TIME] {elapsed:.3f}s")
        print("=" * 70)

        raise

    elapsed = time.perf_counter() - started_at
    result_preview = str(result)

    if len(result_preview) > 1500:
        result_preview = (
                result_preview[:1500]
                + "\n...[result truncated]"
        )

    print(f"[✅ TOOL RESULT] {function_name}")
    print(result_preview)
    print(f"[⏱ TOOL TIME] {elapsed:.3f}s")
    print("=" * 70)

    return result
