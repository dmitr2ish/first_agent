from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticEvaluation:
    """Deterministic, scenario-specific checks for a final benchmark answer."""

    passed: bool
    failure_reasons: tuple[str, ...]


def strip_programmatic_source_appendix(answer: str) -> str:
    """Return only the model-authored body used for semantic evaluation."""

    markers = (
        "\n## Проверенные источники",
        "\n## Verified sources",
    )
    boundary = len(answer)

    for marker in markers:
        position = answer.find(marker)
        if position >= 0:
            boundary = min(boundary, position)

    return answer[:boundary].strip()


def _pattern_matches(pattern: str, text: str) -> bool:
    try:
        return re.search(
            pattern,
            text,
            flags=re.IGNORECASE | re.DOTALL,
        ) is not None
    except re.error as error:
        raise ValueError(
            f"Invalid benchmark expectation regex {pattern!r}: {error}"
        ) from error


def validate_expectations_schema(
    expectations: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    """Validate optional regex groups without coupling them to model code."""

    if expectations is None:
        return {}

    if not isinstance(expectations, dict):
        raise ValueError(f"{field_name} must be an object.")

    supported_fields = {
        "required_pattern_groups",
        "forbidden_patterns",
    }
    unknown_fields = set(expectations) - supported_fields

    if unknown_fields:
        raise ValueError(
            f"{field_name} contains unsupported fields: "
            + ", ".join(sorted(unknown_fields))
        )

    required_groups = expectations.get(
        "required_pattern_groups",
        [],
    )
    forbidden_patterns = expectations.get(
        "forbidden_patterns",
        [],
    )

    if not isinstance(required_groups, list):
        raise ValueError(
            f"{field_name}.required_pattern_groups must be an array."
        )

    normalized_groups: list[list[str]] = []

    for group_index, raw_group in enumerate(required_groups, start=1):
        if not isinstance(raw_group, list) or not raw_group:
            raise ValueError(
                f"{field_name}.required_pattern_groups[{group_index}] "
                "must be a non-empty array."
            )

        normalized_group: list[str] = []
        for pattern in raw_group:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(
                    f"{field_name}.required_pattern_groups[{group_index}] "
                    "contains an invalid regex."
                )
            if _pattern_matches(pattern, ""):
                raise ValueError(
                    f"{field_name}.required_pattern_groups[{group_index}] "
                    "contains a regex that matches empty text."
                )
            normalized_group.append(pattern)

        normalized_groups.append(normalized_group)

    if not isinstance(forbidden_patterns, list):
        raise ValueError(
            f"{field_name}.forbidden_patterns must be an array."
        )

    normalized_forbidden: list[str] = []

    for pattern in forbidden_patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(
                f"{field_name}.forbidden_patterns contains an invalid regex."
            )
        _pattern_matches(pattern, "")
        normalized_forbidden.append(pattern)

    if not normalized_groups and not normalized_forbidden:
        return {}

    return {
        "required_pattern_groups": normalized_groups,
        "forbidden_patterns": normalized_forbidden,
    }


def evaluate_semantic_expectations(
    answer: str,
    expectations: dict[str, Any] | None,
) -> SemanticEvaluation:
    """Evaluate only explicit golden checks; no LLM judge is implied."""

    normalized = validate_expectations_schema(
        expectations,
        field_name="expectations",
    )

    if (
        not normalized
        or (
            not normalized["required_pattern_groups"]
            and not normalized["forbidden_patterns"]
        )
    ):
        return SemanticEvaluation(
            passed=True,
            failure_reasons=(),
        )

    body = strip_programmatic_source_appendix(answer)
    failure_reasons: list[str] = []

    if not body:
        failure_reasons.append("empty_final_body")

    for group_index, group in enumerate(
        normalized["required_pattern_groups"],
        start=1,
    ):
        if not any(
            _pattern_matches(pattern, body)
            for pattern in group
        ):
            failure_reasons.append(
                f"missing_required_pattern_group_{group_index}"
            )

    for pattern_index, pattern in enumerate(
        normalized["forbidden_patterns"],
        start=1,
    ):
        if _pattern_matches(pattern, body):
            failure_reasons.append(
                f"matched_forbidden_pattern_{pattern_index}"
            )

    return SemanticEvaluation(
        passed=not failure_reasons,
        failure_reasons=tuple(failure_reasons),
    )
