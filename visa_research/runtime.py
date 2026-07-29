from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaPreflightError(RuntimeError):
    """Raised when Ollama or the selected model cannot run the workflow."""


@dataclass(frozen=True)
class OllamaModelMetadata:
    """Normalized runtime metadata used by CLI and benchmark reports."""

    name: str
    digest: str
    capabilities: tuple[str, ...]
    server_version: str

    def as_component_versions(self) -> dict[str, str]:
        return {
            "ollama_server": self.server_version or "unknown",
            "ollama_model": self.name,
            "ollama_model_digest": self.digest or "unknown",
            "ollama_model_capabilities": (
                ",".join(self.capabilities)
                if self.capabilities
                else "unknown"
            ),
        }


def _request_json(
    method: str,
    url: str,
    *,
    timeout: float = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            timeout=timeout,
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise OllamaPreflightError(
            "Не удалось подключиться к Ollama. "
            "Запустите Ollama и повторите команду."
        ) from error
    except ValueError as error:
        raise OllamaPreflightError(
            f"Ollama вернула некорректный JSON для {url}."
        ) from error

    if not isinstance(payload, dict):
        raise OllamaPreflightError(
            f"Ollama вернула неожиданный ответ для {url}."
        )

    return payload


def list_ollama_models(
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
) -> list[dict[str, str]]:
    """Return installed model names and digests from the local Ollama API."""

    payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/api/tags",
    )
    raw_models = payload.get("models")

    if not isinstance(raw_models, list):
        return []

    models: list[dict[str, str]] = []

    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue

        name = str(raw_model.get("name") or "").strip()
        if not name:
            continue

        models.append(
            {
                "name": name,
                "digest": str(raw_model.get("digest") or "").strip(),
            }
        )

    return models


def preflight_ollama_model(
    model_name: str,
    *,
    base_url: str = DEFAULT_OLLAMA_URL,
    require_tools: bool = True,
) -> OllamaModelMetadata:
    """Verify model presence/tool capability and capture runtime provenance."""

    clean_name = model_name.strip()
    if not clean_name:
        raise OllamaPreflightError("Имя модели не может быть пустым.")

    installed_models = {
        model["name"]: model
        for model in list_ollama_models(base_url=base_url)
    }

    if clean_name not in installed_models:
        raise OllamaPreflightError(
            f"Модель {clean_name!r} не установлена в Ollama."
        )

    show_payload = _request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/show",
        json={"model": clean_name},
    )
    version_payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/api/version",
    )
    raw_capabilities = show_payload.get("capabilities")
    capabilities = tuple(sorted(
        {
            str(capability).strip().casefold()
            for capability in (
                raw_capabilities
                if isinstance(raw_capabilities, list)
                else []
            )
            if str(capability).strip()
        }
    ))

    if require_tools and "tools" not in capabilities:
        detail = (
            "не объявляет поддержку tool calling"
            if capabilities
            else (
                "не вернула список capabilities, поэтому поддержку "
                "tool calling нельзя подтвердить"
            )
        )
        raise OllamaPreflightError(
            f"Модель {clean_name!r} {detail}."
        )

    digest = (
        str(show_payload.get("digest") or "").strip()
        or installed_models[clean_name]["digest"]
    )
    server_version = str(
        version_payload.get("version") or ""
    ).strip()

    if not digest:
        raise OllamaPreflightError(
            f"Ollama не вернула digest модели {clean_name!r}; "
            "воспроизводимость запуска нельзя подтвердить."
        )

    if not server_version:
        raise OllamaPreflightError(
            "Ollama не вернула версию сервера; "
            "воспроизводимость запуска нельзя подтвердить."
        )

    return OllamaModelMetadata(
        name=clean_name,
        digest=digest,
        capabilities=capabilities,
        server_version=server_version,
    )
