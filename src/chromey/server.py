from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from chromey.chrome import ChromeLaunchOptions, default_launch_user_data_dir, detect_chrome_path, discover_user_data_dir, friendly_profile_labels, launch_chrome, list_profiles, probe_cdp_url, resolve_launch_user_data_dir, wait_for_cdp
from chromey.config import CONFIG_PATHS, RuntimeConfig, normalize_performance_profile, normalize_screenshot_dimension
from chromey.lmstudio import list_models, probe_lmstudio, resolve_model
from chromey.prompting import extract_latest_user_text, format_recent_messages, messages_before_latest_user
from chromey.session import SessionController


def _extension_origin_from_manifest(extension_path: Path) -> str | None:
    manifest_path = extension_path / "manifest.json"
    try:
        manifest_payload = json.loads(manifest_path.read_text())
    except Exception:
        return None

    key = manifest_payload.get("key")
    if not isinstance(key, str) or not key.strip():
        return None

    try:
        public_key_der = base64.b64decode(key.strip())
    except Exception:
        return None

    digest = hashlib.sha256(public_key_der).hexdigest()[:32]
    extension_id = digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
    return f"chrome-extension://{extension_id}"


class LocalOriginOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_extension_origin: str | None = None):
        super().__init__(app)
        self.allowed_extension_origin = (allowed_extension_origin or "").strip().lower()

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "").strip().lower()
        if origin and origin != self.allowed_extension_origin:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Chromey only accepts browser requests from the installed Chromey extension.",
                },
                status_code=403,
            )
        return await call_next(request)


@dataclass
class ProxyServerOptions:
    config: RuntimeConfig
    host: str
    port: int
    log_level: str = "info"
    response_timeout: float = 180.0
    connect_on_start: bool = False


@dataclass
class ProxyContext:
    options: ProxyServerOptions
    started_at: float = field(default_factory=time.time)
    _cdp_probe_deadline: float = 0.0
    _cdp_probe_connected: bool = False

    @property
    def config(self) -> RuntimeConfig:
        return self.options.config

    def uptime_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)


def _probe_browser_connected(context: ProxyContext, *, ttl_seconds: float = 1.0) -> bool:
    now = time.time()
    if now < context._cdp_probe_deadline:
        return context._cdp_probe_connected

    connected = bool(context.config.cdp_url and probe_cdp_url(context.config.cdp_url))
    context._cdp_probe_connected = connected
    context._cdp_probe_deadline = now + ttl_seconds
    return connected


def _browser_hint(context: ProxyContext, snapshot: dict[str, object]) -> tuple[bool, str]:
    config = context.config
    if _probe_browser_connected(context):
        return True, "Chrome CDP is reachable."
    if config.launch_browser:
        return False, "Chrome is not attached yet. Use Start Browser or wait for the launched window to become ready."
    return (
        False,
        "Chrome is not attached. Start Chrome with --remote-debugging-port=9222 or run the proxy with --launch-browser.",
    )


def _session_payload(controller: SessionController, context: ProxyContext) -> dict[str, Any]:
    snapshot = controller.snapshot_data()
    browser_connected, browser_hint = _browser_hint(context, snapshot)
    return {
        "ok": True,
        "status_text": controller.status_text(),
        "snapshot": snapshot,
        "browser_connected": browser_connected,
        "browser_hint": browser_hint,
    }


def _provider_payload(context: ProxyContext) -> dict[str, Any]:
    payload = probe_lmstudio(context.config.endpoint_url, context.config.api_key)
    selected_model = None
    if payload["reachable"]:
        try:
            selected_model = resolve_model(context.config.endpoint_url, context.config.model, context.config.api_key)
        except Exception as exc:
            payload["model_error"] = str(exc)
    payload.update(
        {
            "ok": True,
            "provider": "lmstudio",
            "provider_display_name": "LM Studio",
            "requested_model": context.config.model,
            "selected_model": selected_model,
        }
    )
    return payload


def _browser_payload(controller: SessionController, context: ProxyContext) -> dict[str, Any]:
    snapshot = controller.snapshot_data()
    connected, hint = _browser_hint(context, snapshot)
    chrome_path = detect_chrome_path(context.config.chrome_path)
    if context.config.launch_browser:
        user_data_dir = resolve_launch_user_data_dir(context.config.user_data_dir, chrome_path=chrome_path)
        launch_mode = "isolated-profile" if not context.config.user_data_dir else "explicit-profile"
    else:
        user_data_dir = discover_user_data_dir(context.config.user_data_dir)
        launch_mode = "attach-existing"
    return {
        "ok": True,
        "browser": "chrome",
        "cdp_url": context.config.cdp_url,
        "connected": connected,
        "hint": hint,
        "launch_mode": launch_mode,
        "can_launch": context.config.launch_browser,
        "chrome_path": chrome_path,
        "profile_directory": context.config.profile_directory,
        "user_data_dir": str(user_data_dir) if user_data_dir else None,
        "default_launch_user_data_dir": str(default_launch_user_data_dir(chrome_path)),
        "known_profiles": list_profiles(user_data_dir)[:12] if user_data_dir else [],
        "known_profile_labels": friendly_profile_labels(user_data_dir)[:12] if user_data_dir else [],
    }


def _config_payload(context: ProxyContext) -> dict[str, Any]:
    return {
        "ok": True,
        "runtime": {
            "endpoint_url": context.config.endpoint_url,
            "model": context.config.model,
            "chrome_path": context.config.chrome_path,
            "cdp_url": context.config.cdp_url,
            "user_data_dir": context.config.user_data_dir,
            "artifacts_dir": context.config.artifacts_dir,
            "profile_directory": context.config.profile_directory,
            "launch_browser": context.config.launch_browser,
            "show_profile_picker": context.config.show_profile_picker,
            "max_steps": context.config.max_steps,
            "step_timeout": context.config.step_timeout,
            "use_vision": context.config.use_vision,
            "performance_profile": context.config.performance_profile,
            "screenshot_width": context.config.screenshot_width,
            "screenshot_height": context.config.screenshot_height,
        },
        "config_files": [{"path": str(path), "exists": path.exists()} for path in CONFIG_PATHS],
    }


def _openai_error(message: str, *, error_type: str = "server_error") -> dict[str, object]:
    return {
        "error": {
            "message": message,
            "type": error_type,
        }
    }


def _chat_completion_response(*, text: str, model: str) -> dict[str, object]:
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chat_completion_chunk(*, text: str, model: str) -> bytes:
    payload = {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def _model_list_payload(model_ids: list[str]) -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "owned_by": "lm-studio",
            }
            for model_id in model_ids
        ],
    }


def create_proxy_app(options: ProxyServerOptions) -> Starlette:
    context = ProxyContext(options=options)
    controller = SessionController(options.config)
    launched_process: dict[str, Any] = {"process": None}
    extension_path = Path(__file__).resolve().parents[2] / "extension" / "chromey-extension"
    allowed_extension_origin = _extension_origin_from_manifest(extension_path)

    async def start_browser() -> str:
        if not context.config.launch_browser:
            raise RuntimeError("This server was not started with --launch-browser.")
        if context.config.cdp_url and probe_cdp_url(context.config.cdp_url):
            return "Chrome is already connected."

        process = launch_chrome(
            ChromeLaunchOptions(
                chrome_path=context.config.chrome_path,
                cdp_url=context.config.cdp_url,
                user_data_dir=context.config.user_data_dir,
                profile_directory=context.config.profile_directory,
                show_profile_picker=context.config.show_profile_picker,
                extension_path=extension_path,
            )
        )
        launched_process["process"] = process
        launched_user_data_dir = resolve_launch_user_data_dir(
            context.config.user_data_dir,
            chrome_path=detect_chrome_path(context.config.chrome_path),
        )
        using_explicit_profile = bool(context.config.user_data_dir)
        if context.config.cdp_url and wait_for_cdp(context.config.cdp_url, timeout_seconds=20.0):
            return (
                "Launched Chromey browser and connected. "
                f"User data dir: {launched_user_data_dir}"
            )
        if context.config.show_profile_picker and using_explicit_profile:
            return (
                "Launched Chrome. Pick a profile if prompted, then wait a few seconds and refresh. "
                f"User data dir: {launched_user_data_dir}"
            )
        return (
            "Launched Chromey browser. "
            f"User data dir: {launched_user_data_dir}. "
            "Give it a few seconds, then refresh."
        )

    async def health(_request: Request) -> JSONResponse:
        payload = _session_payload(controller, context)
        payload["provider"] = _provider_payload(context)
        payload["browser"] = _browser_payload(controller, context)
        payload["uptime_seconds"] = round(context.uptime_seconds(), 3)
        return JSONResponse(payload)

    async def v1_models(_request: Request) -> JSONResponse:
        try:
            model_ids = list_models(context.config.endpoint_url, context.config.api_key)
        except Exception as exc:
            return JSONResponse(_openai_error(str(exc), error_type="invalid_request_error"), status_code=503)
        return JSONResponse(_model_list_payload(model_ids))

    async def v1_chat_completions(request: Request):
        payload = await request.json()
        messages = payload.get("messages")
        requested_model = payload.get("model")
        stream = payload.get("stream") is True
        requested_performance_profile = payload.get("performance_profile")
        requested_use_vision = payload.get("use_vision")
        requested_screenshot_width = payload.get("screenshot_width")
        requested_screenshot_height = payload.get("screenshot_height")

        if not isinstance(messages, list):
            return JSONResponse(
                _openai_error("Missing or invalid `messages` payload.", error_type="invalid_request_error"),
                status_code=400,
            )

        normalized_messages = [item for item in messages if isinstance(item, dict)]
        prompt_text = extract_latest_user_text(normalized_messages)
        if not prompt_text:
            return JSONResponse(
                _openai_error("No user message was provided.", error_type="invalid_request_error"),
                status_code=400,
            )

        conversation_context = format_recent_messages(
            messages_before_latest_user(normalized_messages),
            current_request="",
        )

        try:
            effective_config = replace(
                context.config,
                performance_profile=normalize_performance_profile(requested_performance_profile)
                if requested_performance_profile is not None
                else context.config.performance_profile,
                use_vision=bool(requested_use_vision)
                if isinstance(requested_use_vision, bool)
                else context.config.use_vision,
                screenshot_width=normalize_screenshot_dimension(requested_screenshot_width)
                if requested_screenshot_width is not None
                else context.config.screenshot_width,
                screenshot_height=normalize_screenshot_dimension(requested_screenshot_height)
                if requested_screenshot_height is not None
                else context.config.screenshot_height,
            )
            resolved_model = resolve_model(
                effective_config.endpoint_url,
                requested_model.strip() if isinstance(requested_model, str) and requested_model.strip() else effective_config.model,
                effective_config.api_key,
            )
            reply = await asyncio.wait_for(
                controller.handle_instruction(
                    prompt_text.strip(),
                    model_name=resolved_model,
                    conversation_context=conversation_context,
                    run_config=effective_config,
                ),
                timeout=options.response_timeout,
            )
        except Exception as exc:
            return JSONResponse(_openai_error(str(exc)), status_code=500)

        if stream:

            async def event_stream():
                yield _chat_completion_chunk(text=reply, model=resolved_model)
                final_chunk = {
                    "id": f"chatcmpl-{uuid4().hex}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": resolved_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(final_chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        return JSONResponse(_chat_completion_response(text=reply, model=resolved_model))

    async def config_view(_request: Request) -> JSONResponse:
        return JSONResponse(_config_payload(context))

    async def provider_view(_request: Request) -> JSONResponse:
        payload = _provider_payload(context)
        if payload["reachable"]:
            payload["models"] = list_models(context.config.endpoint_url, context.config.api_key)
        return JSONResponse(payload)

    async def browser_view(_request: Request) -> JSONResponse:
        payload = _browser_payload(controller, context)
        process = launched_process["process"]
        payload["launched_browser_pid"] = process.pid if process is not None else None
        payload["launched_browser_running"] = bool(process is not None and process.poll() is None)
        return JSONResponse(payload)

    async def browser_start(_request: Request) -> JSONResponse:
        try:
            reply = await start_browser()
            payload = _browser_payload(controller, context)
            payload["reply"] = reply
            return JSONResponse(payload)
        except Exception as exc:
            payload = _browser_payload(controller, context)
            payload["ok"] = False
            payload["error"] = str(exc)
            return JSONResponse(payload, status_code=200)

    async def session_status(_request: Request) -> JSONResponse:
        return JSONResponse(_session_payload(controller, context))

    async def session_message(request: Request) -> JSONResponse:
        payload = await request.json()
        prompt = payload.get("message") or payload.get("prompt")
        model_name = payload.get("model")
        history = payload.get("history")
        requested_performance_profile = payload.get("performance_profile")
        requested_use_vision = payload.get("use_vision")
        requested_screenshot_width = payload.get("screenshot_width")
        requested_screenshot_height = payload.get("screenshot_height")
        if not isinstance(prompt, str) or not prompt.strip():
            return JSONResponse({"ok": False, "error": "Missing `message` in request payload."}, status_code=400)

        conversation_context = ""
        if isinstance(history, list):
            conversation_context = format_recent_messages(
                [item for item in history if isinstance(item, dict)],
                current_request=prompt.strip(),
            )

        try:
            effective_config = replace(
                context.config,
                performance_profile=normalize_performance_profile(requested_performance_profile)
                if requested_performance_profile is not None
                else context.config.performance_profile,
                use_vision=bool(requested_use_vision)
                if isinstance(requested_use_vision, bool)
                else context.config.use_vision,
                screenshot_width=normalize_screenshot_dimension(requested_screenshot_width)
                if requested_screenshot_width is not None
                else context.config.screenshot_width,
                screenshot_height=normalize_screenshot_dimension(requested_screenshot_height)
                if requested_screenshot_height is not None
                else context.config.screenshot_height,
            )
            reply = await asyncio.wait_for(
                controller.handle_instruction(
                    prompt.strip(),
                    model_name=model_name.strip() if isinstance(model_name, str) and model_name.strip() else None,
                    conversation_context=conversation_context,
                    run_config=effective_config,
                ),
                timeout=options.response_timeout,
            )
        except Exception as exc:
            response = _session_payload(controller, context)
            response["ok"] = False
            response["error"] = str(exc)
            return JSONResponse(response, status_code=200)

        response = _session_payload(controller, context)
        response["reply"] = reply
        return JSONResponse(response)

    async def session_stop(_request: Request) -> JSONResponse:
        reply = await controller.stop_current()
        response = _session_payload(controller, context)
        response["reply"] = reply
        return JSONResponse(response)

    async def session_reset(_request: Request) -> JSONResponse:
        reply = await controller.reset_session()
        response = _session_payload(controller, context)
        response["reply"] = reply
        return JSONResponse(response)

    async def startup() -> None:
        if context.config.launch_browser:
            await start_browser()
            return

        if options.connect_on_start:
            await controller.ensure_browser()

    async def shutdown() -> None:
        await controller.shutdown()

    routes = [
        Route("/health", health),
        Route("/api/health", health),
        Route("/v1/models", v1_models),
        Route("/v1/chat/completions", v1_chat_completions, methods=["POST"]),
        Route("/api/config", config_view),
        Route("/api/provider", provider_view),
        Route("/api/browser", browser_view),
        Route("/api/browser/start", browser_start, methods=["POST"]),
        Route("/api/session", session_status),
        Route("/api/session/message", session_message, methods=["POST"]),
        Route("/api/session/stop", session_stop, methods=["POST"]),
        Route("/api/session/reset", session_reset, methods=["POST"]),
    ]
    middleware = [
        Middleware(LocalOriginOnlyMiddleware, allowed_extension_origin=allowed_extension_origin),
        Middleware(
            CORSMiddleware,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_origins=[allowed_extension_origin] if allowed_extension_origin else [],
        ),
    ]
    app = Starlette(routes=routes, middleware=middleware, on_startup=[startup], on_shutdown=[shutdown])
    app.state.launched_browser_process = launched_process
    return app


def run_proxy_server(options: ProxyServerOptions) -> int:
    app = create_proxy_app(options)
    uvicorn.run(
        app,
        host=options.host,
        port=options.port,
        log_level=options.log_level,
        log_config=None,
        access_log=False,
    )
    return 0
