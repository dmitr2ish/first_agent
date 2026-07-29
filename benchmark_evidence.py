from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from visa_workflow import (
    SourcePackageOverrideError,
    validate_source_package_override,
)


EVIDENCE_CACHE_SCHEMA_VERSION = "1"
SOURCE_COLLECTION_STEP = "collect_official_sources"


class EvidenceCacheError(RuntimeError):
    """Raised when frozen benchmark evidence is incomplete or untrusted."""


@dataclass(frozen=True)
class EvidenceTurn:
    """Stable identity of one benchmark turn bound to its exact prompt."""

    scenario_id: str
    turn_id: str
    prompt_sha256: str

    @property
    def key(self) -> str:
        return json.dumps(
            [self.scenario_id, self.turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_prompt(
        cls,
        *,
        scenario_id: str,
        turn_id: str,
        prompt: str,
    ) -> EvidenceTurn:
        return cls(
            scenario_id=scenario_id,
            turn_id=turn_id,
            prompt_sha256=_sha256_text(prompt),
        )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise EvidenceCacheError(
            f"Evidence cache contains non-JSON data: {error}"
        ) from error


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _manifest_digest(manifest: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    return _sha256_json(unsigned)


def _clone_json(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _validate_source_package(value: Any) -> dict[str, Any]:
    try:
        return validate_source_package_override(value)
    except SourcePackageOverrideError as error:
        raise EvidenceCacheError(
            f"Frozen source package is not replayable: {error}"
        ) from error


def _iter_step_results(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_step_results(item)
        return

    if value is not None:
        yield value


def extract_source_package(workflow_response: Any) -> dict[str, Any]:
    """
    Extract the exact persisted collector output from WorkflowRunOutput.

    Recording from the completed step output avoids rerunning retrieval or
    reconstructing a package from reporter previews.
    """

    matching_packages: list[dict[str, Any]] = []
    step_results = getattr(workflow_response, "step_results", None)

    for step_result in _iter_step_results(step_results):
        step_name = (
            getattr(step_result, "step_name", None)
            or getattr(step_result, "step_id", None)
        )
        if step_name != SOURCE_COLLECTION_STEP:
            continue

        content = getattr(step_result, "content", None)
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError as error:
                raise EvidenceCacheError(
                    "Source collector output is not valid JSON."
                ) from error

        if not isinstance(content, dict):
            raise EvidenceCacheError(
                "Source collector output must be a JSON object."
            )

        sources = content.get("sources")
        if not isinstance(sources, list):
            raise EvidenceCacheError(
                "Source collector output has no sources array."
            )

        matching_packages.append(
            _validate_source_package(_clone_json(content))
        )

    if not matching_packages:
        raise EvidenceCacheError(
            "Workflow response has no collect_official_sources output; "
            "the exact evidence package cannot be recorded."
        )

    first_package = matching_packages[0]
    if any(
        package != first_package
        for package in matching_packages[1:]
    ):
        raise EvidenceCacheError(
            "Workflow response contains conflicting source packages."
        )

    return first_package


class BenchmarkEvidenceCache:
    """Atomic record/replay store for model-independent benchmark evidence."""

    def __init__(
        self,
        *,
        mode: str,
        path: str | Path,
        benchmark_version: str,
        benchmark_digest: str,
        source_collector_version: str,
        workflow_version: str,
        expected_turns: Iterable[EvidenceTurn],
    ) -> None:
        if mode not in {"record", "replay"}:
            raise ValueError("Evidence cache mode must be record or replay.")

        self.mode = mode
        self.path = Path(path)
        self.benchmark_version = benchmark_version
        self.benchmark_digest = benchmark_digest
        self.source_collector_version = source_collector_version
        self.workflow_version = workflow_version
        self.expected_turns = tuple(expected_turns)
        self._expected_by_key = {
            turn.key: turn
            for turn in self.expected_turns
        }

        if len(self._expected_by_key) != len(self.expected_turns):
            raise EvidenceCacheError(
                "Benchmark contains duplicate scenario/turn identities."
            )

        if mode == "record":
            if self.path.exists():
                raise EvidenceCacheError(
                    f"Evidence cache already exists: {self.path}"
                )
            self._manifest = self._new_manifest()
            self._write_manifest()
        else:
            self._manifest = self._load_manifest()
            self._validate_manifest(require_complete=True)

    @property
    def manifest_sha256(self) -> str:
        return str(self._manifest["manifest_sha256"])

    def _new_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "schema_version": EVIDENCE_CACHE_SCHEMA_VERSION,
            "benchmark_version": self.benchmark_version,
            "benchmark_digest": self.benchmark_digest,
            "source_collector_version": self.source_collector_version,
            "workflow_version": self.workflow_version,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "complete": False,
            "entries": {},
        }
        manifest["manifest_sha256"] = _manifest_digest(manifest)
        return manifest

    def _load_manifest(self) -> dict[str, Any]:
        try:
            with self.path.open(encoding="utf-8") as cache_file:
                manifest: Any = json.load(cache_file)
        except FileNotFoundError as error:
            raise EvidenceCacheError(
                f"Evidence cache does not exist: {self.path}"
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise EvidenceCacheError(
                f"Cannot read evidence cache {self.path}: {error}"
            ) from error

        if not isinstance(manifest, dict):
            raise EvidenceCacheError(
                "Evidence cache root must be a JSON object."
            )

        return manifest

    def _validate_manifest(self, *, require_complete: bool) -> None:
        stored_digest = self._manifest.get("manifest_sha256")
        if (
            not isinstance(stored_digest, str)
            or stored_digest != _manifest_digest(self._manifest)
        ):
            raise EvidenceCacheError(
                "Evidence cache manifest hash mismatch."
            )

        if (
            self._manifest.get("schema_version")
            != EVIDENCE_CACHE_SCHEMA_VERSION
        ):
            raise EvidenceCacheError(
                "Unsupported evidence cache schema version."
            )
        if (
            self._manifest.get("benchmark_version")
            != self.benchmark_version
        ):
            raise EvidenceCacheError(
                "Evidence cache benchmark version mismatch."
            )
        if (
            self._manifest.get("benchmark_digest")
            != self.benchmark_digest
        ):
            raise EvidenceCacheError(
                "Evidence cache benchmark digest mismatch."
            )
        if (
            self._manifest.get("source_collector_version")
            != self.source_collector_version
        ):
            raise EvidenceCacheError(
                "Evidence cache source collector version mismatch."
            )
        if self._manifest.get("workflow_version") != self.workflow_version:
            raise EvidenceCacheError(
                "Evidence cache workflow version mismatch."
            )
        if require_complete and self._manifest.get("complete") is not True:
            raise EvidenceCacheError(
                "Evidence cache is incomplete."
            )

        entries = self._manifest.get("entries")
        if not isinstance(entries, dict):
            raise EvidenceCacheError(
                "Evidence cache entries must be a JSON object."
            )

        expected_keys = set(self._expected_by_key)
        actual_keys = set(entries)
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)

        if missing:
            raise EvidenceCacheError(
                "Evidence cache is missing benchmark turns: "
                + ", ".join(missing)
            )
        if unexpected:
            raise EvidenceCacheError(
                "Evidence cache contains unexpected benchmark turns: "
                + ", ".join(unexpected)
            )

        for key, expected_turn in self._expected_by_key.items():
            entry = entries.get(key)
            if not isinstance(entry, dict):
                raise EvidenceCacheError(
                    f"Evidence entry {key} must be an object."
                )
            if (
                entry.get("scenario_id") != expected_turn.scenario_id
                or entry.get("turn_id") != expected_turn.turn_id
                or entry.get("prompt_sha256")
                != expected_turn.prompt_sha256
            ):
                raise EvidenceCacheError(
                    f"Evidence entry identity mismatch for {key}."
                )

            source_package = entry.get("source_package")
            if not isinstance(source_package, dict):
                raise EvidenceCacheError(
                    f"Evidence entry {key} has no source package."
                )
            if not isinstance(source_package.get("sources"), list):
                raise EvidenceCacheError(
                    f"Evidence entry {key} has no sources array."
                )
            _validate_source_package(source_package)

            package_sha256 = entry.get("package_sha256")
            if (
                not isinstance(package_sha256, str)
                or package_sha256 != _sha256_json(source_package)
            ):
                raise EvidenceCacheError(
                    f"Evidence package hash mismatch for {key}."
                )

    def record_response(
        self,
        *,
        turn: EvidenceTurn,
        workflow_response: Any,
    ) -> None:
        if self.mode != "record":
            raise EvidenceCacheError(
                "Cannot record into a replay evidence cache."
            )

        expected = self._expected_by_key.get(turn.key)
        if expected != turn:
            raise EvidenceCacheError(
                f"Turn is not part of this benchmark: {turn.key}"
            )

        entries = self._manifest["entries"]
        if turn.key in entries:
            raise EvidenceCacheError(
                f"Evidence was already recorded for {turn.key}."
            )

        source_package = extract_source_package(workflow_response)
        entries[turn.key] = {
            "scenario_id": turn.scenario_id,
            "turn_id": turn.turn_id,
            "prompt_sha256": turn.prompt_sha256,
            "package_sha256": _sha256_json(source_package),
            "source_package": source_package,
        }
        self._manifest["manifest_sha256"] = _manifest_digest(
            self._manifest
        )
        self._write_manifest()

    def source_package_for(self, *, turn: EvidenceTurn) -> dict[str, Any]:
        if self.mode != "replay":
            raise EvidenceCacheError(
                "Source packages can only be replayed in replay mode."
            )

        expected = self._expected_by_key.get(turn.key)
        if expected != turn:
            raise EvidenceCacheError(
                f"Turn is not part of this benchmark: {turn.key}"
            )

        entry = self._manifest["entries"][turn.key]
        return _validate_source_package(
            _clone_json(entry["source_package"])
        )

    def finalize(self) -> None:
        if self.mode != "record":
            return

        # Validate the complete turn set while the persisted state is still
        # explicitly incomplete. A failed early finalize must not poison the
        # in-memory state used by later record_response calls.
        self._validate_manifest(require_complete=False)
        self._manifest["complete"] = True
        self._manifest["manifest_sha256"] = _manifest_digest(
            self._manifest
        )
        self._validate_manifest(require_complete=True)
        self._write_manifest()

    def _write_manifest(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    self._manifest,
                    temporary,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                temporary.write("\n")

            os.replace(temporary_path, self.path)
        finally:
            if (
                temporary_path is not None
                and temporary_path.exists()
            ):
                temporary_path.unlink()
