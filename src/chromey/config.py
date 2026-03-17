from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "chromey"
DEFAULT_ARTIFACTS_DIR = CONFIG_DIR / "artifacts"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG_JSON_PATH = PROJECT_ROOT / "config.json"
PROJECT_CONFIG_YAML_PATH = PROJECT_ROOT / "config.yaml"
PROJECT_CONFIG_YML_PATH = PROJECT_ROOT / "config.yml"
CONFIG_JSON_PATH = CONFIG_DIR / "config.json"
CONFIG_YAML_PATH = CONFIG_DIR / "config.yaml"
CONFIG_YML_PATH = CONFIG_DIR / "config.yml"
CONFIG_PATHS = (
    CONFIG_JSON_PATH,
    CONFIG_YAML_PATH,
    CONFIG_YML_PATH,
    PROJECT_CONFIG_JSON_PATH,
    PROJECT_CONFIG_YAML_PATH,
    PROJECT_CONFIG_YML_PATH,
)

DEFAULT_ENDPOINT_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "auto"
DEFAULT_PERFORMANCE_PROFILE = "balanced"


def normalize_endpoint_url(url: str) -> str:
    normalized = (url or DEFAULT_ENDPOINT_URL).strip().rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def resolve_api_key(api_key: str | None) -> str:
    provided = str(api_key or "").strip()
    if provided:
        return provided
    return os.environ.get("LM_STUDIO_API_KEY") or os.environ.get("MODEL_API_KEY") or "lm-studio"


def normalize_performance_profile(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"fast", "speed", "performance"}:
        return "fast"
    return DEFAULT_PERFORMANCE_PROFILE


def normalize_screenshot_dimension(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized < 256:
        return 256
    if normalized > 2048:
        return 2048
    return normalized


@dataclass
class GlobalConfig:
    endpoint_url: str = DEFAULT_ENDPOINT_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    chrome_path: str = ""
    cdp_url: str = ""
    user_data_dir: str = ""
    artifacts_dir: str = ""
    profile_directory: str = "Default"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 8089
    launch_browser: bool = False
    show_profile_picker: bool = False
    headless: bool = False
    max_steps: int = 25
    step_timeout: int = 120
    max_failures: int | None = 2
    max_actions_per_step: int | None = None
    max_completion_tokens: int | None = None
    llm_timeout: int | None = None
    use_vision: bool | str = True
    performance_profile: str = DEFAULT_PERFORMANCE_PROFILE
    screenshot_width: int | None = None
    screenshot_height: int | None = None


@dataclass
class RuntimeConfig:
    endpoint_url: str = DEFAULT_ENDPOINT_URL
    api_key: str = "lm-studio"
    model: str = DEFAULT_MODEL
    chrome_path: str | None = None
    cdp_url: str | None = None
    user_data_dir: str | None = None
    artifacts_dir: str | None = None
    profile_directory: str = "Default"
    headless: bool = False
    max_steps: int = 25
    step_timeout: int = 120
    max_failures: int | None = 2
    max_actions_per_step: int | None = None
    max_completion_tokens: int | None = None
    llm_timeout: int | None = None
    launch_browser: bool = False
    show_profile_picker: bool = False
    viewport_width: int = 1440
    viewport_height: int = 960
    use_vision: bool | str = True
    performance_profile: str = DEFAULT_PERFORMANCE_PROFILE
    screenshot_width: int | None = None
    screenshot_height: int | None = None


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            f"Found YAML config at {path}, but PyYAML is not installed. Use JSON config or install dependencies."
        ) from exc

    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a mapping object in {path}.")
    return payload


def _load_config_file(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        payload = json.loads(path.read_text() or "{}")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected a JSON object in {path}.")
        return payload
    return _load_yaml(path)


def load_global_config() -> GlobalConfig:
    payload: dict[str, Any] = {}
    for path in CONFIG_PATHS:
        if path.exists():
            payload = _merge_dict(payload, _load_config_file(path))
    return GlobalConfig(**{**GlobalConfig().__dict__, **payload})


def build_runtime_config(args, *, for_proxy: bool) -> RuntimeConfig:
    global_config = load_global_config()
    endpoint_url = normalize_endpoint_url(getattr(args, "endpoint_url", "") or global_config.endpoint_url)
    cdp_url = getattr(args, "cdp_url", None) or global_config.cdp_url or None
    launch_browser = bool(getattr(args, "launch_browser", False) or global_config.launch_browser)
    show_profile_picker = bool(getattr(args, "show_profile_picker", False) or global_config.show_profile_picker)

    from chromey.chrome import DEFAULT_CDP_URL

    if for_proxy and launch_browser and not cdp_url:
        cdp_url = DEFAULT_CDP_URL

    return RuntimeConfig(
        endpoint_url=endpoint_url,
        api_key=resolve_api_key(getattr(args, "api_key", "") or global_config.api_key),
        model=getattr(args, "model", "") or global_config.model or DEFAULT_MODEL,
        chrome_path=getattr(args, "chrome_path", None) or global_config.chrome_path or None,
        cdp_url=cdp_url.rstrip("/") if isinstance(cdp_url, str) and cdp_url.strip() else None,
        user_data_dir=getattr(args, "user_data_dir", None) or global_config.user_data_dir or None,
        artifacts_dir=getattr(args, "artifacts_dir", None) or global_config.artifacts_dir or str(DEFAULT_ARTIFACTS_DIR),
        profile_directory=getattr(args, "profile_directory", None) or global_config.profile_directory or "Default",
        headless=bool(getattr(args, "headless", False) or global_config.headless),
        max_steps=int(getattr(args, "max_steps", None) or global_config.max_steps or 25),
        step_timeout=int(getattr(args, "step_timeout", None) or global_config.step_timeout or 120),
        max_failures=getattr(args, "max_failures", None) if getattr(args, "max_failures", None) is not None else global_config.max_failures,
        max_actions_per_step=getattr(args, "max_actions_per_step", None)
        if getattr(args, "max_actions_per_step", None) is not None
        else global_config.max_actions_per_step,
        max_completion_tokens=getattr(args, "max_completion_tokens", None)
        if getattr(args, "max_completion_tokens", None) is not None
        else global_config.max_completion_tokens,
        llm_timeout=getattr(args, "llm_timeout", None) if getattr(args, "llm_timeout", None) is not None else global_config.llm_timeout,
        use_vision=getattr(args, "use_vision", None)
        if getattr(args, "use_vision", None) is not None
        else global_config.use_vision,
        performance_profile=normalize_performance_profile(
            getattr(args, "performance_profile", None)
            if getattr(args, "performance_profile", None) is not None
            else global_config.performance_profile
        ),
        screenshot_width=normalize_screenshot_dimension(
            getattr(args, "screenshot_width", None)
            if getattr(args, "screenshot_width", None) is not None
            else global_config.screenshot_width
        ),
        screenshot_height=normalize_screenshot_dimension(
            getattr(args, "screenshot_height", None)
            if getattr(args, "screenshot_height", None) is not None
            else global_config.screenshot_height
        ),
        launch_browser=launch_browser,
        show_profile_picker=show_profile_picker,
    )
