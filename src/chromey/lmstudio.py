from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

from browser_use import ChatOpenAI

from chromey.config import DEFAULT_ENDPOINT_URL, RuntimeConfig, normalize_endpoint_url

AUTO_MODEL_ALIASES = {"", "auto", "current", "loaded"}
MODELS_CACHE_TTL_SECONDS = 60.0
_MODELS_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
DEFAULT_AGENT_COMPLETION_TOKENS = 1024
FAST_AGENT_COMPLETION_TOKENS = 384


def resolve_llm_timeout(config: RuntimeConfig) -> int:
    configured = config.llm_timeout
    if isinstance(configured, int) and configured > 0:
        if isinstance(config.step_timeout, int) and config.step_timeout > 15:
            return min(configured, config.step_timeout - 10)
        return configured

    if isinstance(config.step_timeout, int) and config.step_timeout > 15:
        return max(30, config.step_timeout - 10)
    return 110


def resolve_completion_tokens(config: RuntimeConfig) -> int:
    if isinstance(config.max_completion_tokens, int) and config.max_completion_tokens > 0:
        return config.max_completion_tokens
    if getattr(config, "performance_profile", "balanced") == "fast":
        return FAST_AGENT_COMPLETION_TOKENS
    return DEFAULT_AGENT_COMPLETION_TOKENS


def pick_auto_model(available_models: list[str]) -> str:
    iq4_match = next((model for model in available_models if "iq4" in model.lower()), None)
    return iq4_match or available_models[0]


def http_get_json(url: str, *, timeout: float = 3.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _auth_headers(api_key: str | None) -> dict[str, str]:
    resolved = (api_key or "").strip()
    return {"Authorization": f"Bearer {resolved}"} if resolved else {}


def list_models(endpoint_url: str, api_key: str | None = None) -> list[str]:
    base_url = normalize_endpoint_url(endpoint_url or DEFAULT_ENDPOINT_URL)
    cache_key = (base_url, (api_key or "").strip())
    now = time.time()
    cached = _MODELS_CACHE.get(cache_key)
    if cached and now < cached[0]:
        return list(cached[1])

    models_url = f"{base_url}/models"
    try:
        payload = http_get_json(models_url, timeout=3.0, headers=_auth_headers(api_key))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"LM Studio returned HTTP {exc.code} while listing models at {models_url}: {body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LM Studio is not reachable at {models_url}.") from exc

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected LM Studio response from {models_url}: {payload}")
    model_ids = [str(item.get("id")).strip() for item in data if isinstance(item, dict) and str(item.get("id", "")).strip()]
    _MODELS_CACHE[cache_key] = (now + MODELS_CACHE_TTL_SECONDS, model_ids)
    return list(model_ids)


def resolve_model(endpoint_url: str, requested_model: str | None, api_key: str | None = None) -> str:
    available_models = list_models(endpoint_url, api_key=api_key)
    if not available_models:
        raise RuntimeError(
            "LM Studio is reachable but no model is loaded on its local server. Load a model in LM Studio first."
        )

    requested = str(requested_model or "").strip()
    if requested.lower() in AUTO_MODEL_ALIASES:
        return pick_auto_model(available_models)
    if requested in available_models:
        return requested

    available_text = ", ".join(available_models[:8])
    raise RuntimeError(
        f"Requested model '{requested}' is not currently loaded in LM Studio. Loaded model ids: {available_text}."
    )


def probe_lmstudio(endpoint_url: str, api_key: str | None = None) -> dict[str, Any]:
    base_url = normalize_endpoint_url(endpoint_url or DEFAULT_ENDPOINT_URL)
    try:
        model_ids = list_models(base_url, api_key=api_key)
    except Exception as exc:
        return {
            "reachable": False,
            "status": "error",
            "message": str(exc),
            "endpoint_url": base_url,
            "models": [],
        }

    return {
        "reachable": True,
        "status": "ok",
        "message": "LM Studio is reachable.",
        "endpoint_url": base_url,
        "models": model_ids,
    }


def build_llm(config: RuntimeConfig, *, model_override: str | None = None) -> ChatOpenAI:
    model = resolve_model(config.endpoint_url, model_override or config.model, api_key=config.api_key)
    llm_timeout = resolve_llm_timeout(config)
    return ChatOpenAI(
        base_url=config.endpoint_url,
        api_key=config.api_key or None,
        model=model,
        temperature=0,
        frequency_penalty=None,
        timeout=llm_timeout,
        max_retries=1,
        max_completion_tokens=resolve_completion_tokens(config),
        add_schema_to_system_prompt=True,
        dont_force_structured_output=False,
        remove_defaults_from_schema=True,
        remove_min_items_from_schema=True,
    )


async def close_llm_client(client: object) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result
