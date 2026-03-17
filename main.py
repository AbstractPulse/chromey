from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from chromey.chrome import DEFAULT_CDP_URL, DEFAULT_CHROME_PATH_CANDIDATES, default_launch_extension_dir, detect_chrome_path, discover_user_data_dir, flatpak_app_id, list_profiles
from chromey.config import DEFAULT_ENDPOINT_URL, DEFAULT_MODEL, build_runtime_config, load_global_config
from chromey.lmstudio import probe_lmstudio, resolve_model
from chromey.server import ProxyServerOptions, run_proxy_server


def configure_logging(log_level: str = "info") -> None:
    level_name = str(log_level or "info").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)-8s [%(name)s] %(message)s",
    )

    for logger_name in [
        "chromey",
        "browser_use.agent.service",
        "browser_use.agent.prompts",
        "browser_use.agent.message_manager.service",
        "browser_use.browser.watchdogs.dom_watchdog",
    ]:
        logging.getLogger(logger_name).setLevel(level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def extension_path_command() -> int:
    chrome_path = detect_chrome_path(None)
    if flatpak_app_id(chrome_path):
        print(str(default_launch_extension_dir(chrome_path)))
        return 0
    print(str(PROJECT_ROOT / "extension" / "chromey-extension"))
    return 0


def detect_chrome_command(args: argparse.Namespace) -> int:
    chrome_path = detect_chrome_path(args.chrome_path)
    user_data_dir = discover_user_data_dir(args.user_data_dir)
    payload = {
        "chrome_path": chrome_path,
        "user_data_dir": str(user_data_dir) if user_data_dir else None,
        "profiles": list_profiles(user_data_dir) if user_data_dir else [],
        "default_cdp_url": DEFAULT_CDP_URL,
        "path_candidates": list(DEFAULT_CHROME_PATH_CANDIDATES),
    }
    print(json.dumps(payload, indent=2))
    return 0


def provider_check_command(args: argparse.Namespace) -> int:
    config = build_runtime_config(args, for_proxy=False)
    payload = probe_lmstudio(config.endpoint_url, config.api_key)
    if payload["reachable"]:
        try:
            payload["selected_model"] = resolve_model(config.endpoint_url, config.model, config.api_key)
        except Exception as exc:
            payload["selected_model_error"] = str(exc)
    print(json.dumps(payload, indent=2))
    return 0 if payload["reachable"] else 1


def proxy_command(args: argparse.Namespace) -> int:
    configure_logging(args.log_level)
    config = build_runtime_config(args, for_proxy=True)
    return run_proxy_server(
        ProxyServerOptions(
            config=config,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            response_timeout=args.response_timeout,
            connect_on_start=args.connect_on_start,
        )
    )


async def run_command(args: argparse.Namespace) -> int:
    configure_logging("info")
    from chromey.browser_use_runtime import build_agent, build_browser
    from chromey.lmstudio import build_llm, close_llm_client

    config = build_runtime_config(args, for_proxy=False)
    browser = build_browser(config, keep_alive=False)
    llm = build_llm(config)
    client = llm.get_client()

    try:
        await browser.start()
        agent = build_agent(config, task=args.task, browser=browser, llm=llm)
        history = await agent.run(max_steps=config.max_steps)
    finally:
        await browser.stop()
        await close_llm_client(client)

    result = {
        "success": history.is_successful(),
        "steps": len(history),
        "final_result": history.final_result(),
        "errors": [error for error in history.errors() if error],
    }
    print(json.dumps(result, indent=2))
    if history.final_result():
        print(history.final_result())
    return 0 if history.is_successful() else 1


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    global_config = load_global_config()
    parser.add_argument("--endpoint-url", default=global_config.endpoint_url or DEFAULT_ENDPOINT_URL)
    parser.add_argument("--api-key", default=global_config.api_key or "")
    parser.add_argument("--model", default=global_config.model or DEFAULT_MODEL)
    parser.add_argument("--chrome-path", default=global_config.chrome_path or None)
    parser.add_argument("--cdp-url", default=global_config.cdp_url or None)
    parser.add_argument("--user-data-dir", default=global_config.user_data_dir or None)
    parser.add_argument("--artifacts-dir", default=global_config.artifacts_dir or None)
    parser.add_argument("--profile-directory", default=global_config.profile_directory or "Default")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=bool(global_config.headless),
        help="Reserved for future direct-launch flows. The current v2 path expects an attached Chrome CDP session.",
    )
    parser.add_argument("--max-steps", type=int, default=global_config.max_steps or 25)
    parser.add_argument("--step-timeout", type=int, default=global_config.step_timeout or 120)
    parser.add_argument("--max-failures", type=int, default=global_config.max_failures)
    parser.add_argument("--max-actions-per-step", type=int, default=global_config.max_actions_per_step)
    parser.add_argument("--max-completion-tokens", type=int, default=global_config.max_completion_tokens)
    parser.add_argument("--llm-timeout", type=int, default=global_config.llm_timeout)
    parser.add_argument(
        "--use-vision",
        action=argparse.BooleanOptionalAction,
        default=global_config.use_vision if isinstance(global_config.use_vision, bool) else True,
        help="Always include browser screenshots in model input. Recommended for Chromey.",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chromey v2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    proxy_parser = subparsers.add_parser("proxy", help="Run the Chromey server.")
    add_common_arguments(proxy_parser)
    proxy_parser.add_argument("--host", default=load_global_config().proxy_host or "127.0.0.1")
    proxy_parser.add_argument("--port", type=int, default=load_global_config().proxy_port or 8089)
    proxy_parser.add_argument("--log-level", default="info")
    proxy_parser.add_argument("--response-timeout", type=float, default=180.0)
    proxy_parser.add_argument(
        "--launch-browser",
        action=argparse.BooleanOptionalAction,
        default=bool(load_global_config().launch_browser),
        help=f"Launch Chrome with remote debugging on startup or on demand. Defaults CDP to {DEFAULT_CDP_URL}.",
    )
    proxy_parser.add_argument(
        "--show-profile-picker",
        action=argparse.BooleanOptionalAction,
        default=bool(load_global_config().show_profile_picker),
        help="Show Chrome's native profile picker when launching Chrome.",
    )
    proxy_parser.add_argument(
        "--connect-on-start",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Try to attach to the configured Chrome session during server startup.",
    )

    run_parser = subparsers.add_parser("run", help="Run a one-off browser-use task.")
    add_common_arguments(run_parser)
    run_parser.add_argument("task")

    detect_parser = subparsers.add_parser("detect-chrome", help="Print detected Chrome paths and profiles.")
    detect_parser.add_argument("--chrome-path", default=None)
    detect_parser.add_argument("--user-data-dir", default=None)

    provider_parser = subparsers.add_parser("provider-check", help="Probe LM Studio and list models.")
    add_common_arguments(provider_parser)

    subparsers.add_parser("extension-path", help="Print the unpacked Chrome extension directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        if args.command == "proxy":
            return proxy_command(args)
        if args.command == "run":
            return asyncio.run(run_command(args))
        if args.command == "detect-chrome":
            return detect_chrome_command(args)
        if args.command == "provider-check":
            return provider_check_command(args)
        if args.command == "extension-path":
            return extension_path_command()
        raise RuntimeError(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
