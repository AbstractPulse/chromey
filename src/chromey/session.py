from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from browser_use.agent.views import AgentState

from chromey.browser_use_runtime import build_agent, build_browser, resolve_agent_performance_settings
from chromey.config import DEFAULT_ARTIFACTS_DIR, RuntimeConfig
from chromey.lmstudio import build_llm, close_llm_client

logger = logging.getLogger(__name__)

REPEATED_ACTION_LIMIT = 3
REPEATED_WAIT_LIMIT = 4
STALLED_PAGE_LIMIT = 4
STALLED_WAIT_LIMIT = 5
BROWSER_CONTEXT_TIMEOUT_SECONDS = 10.0
CANCEL_GRACE_TIMEOUT_SECONDS = 0.75
BROWSER_RESET_TIMEOUT_SECONDS = 1.5
BROWSER_START_TIMEOUT_SECONDS = 8.0
CONTENT_FOCUS_TIMEOUT_SECONDS = 8.5
TASK_BOOTSTRAP_TIMEOUT_SECONDS = 12.0


def _is_extension_url(url: str | None) -> bool:
    return str(url or "").startswith("chrome-extension://")


def _is_browser_internal_url(url: str | None) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    if value in {"about:blank", "chrome://newtab/", "chrome://newtab", "chrome://new-tab-page/", "chrome://new-tab-page"}:
        return True
    return value.startswith("chrome://")


def _extract_hard_constraints(prompt_text: str) -> list[str]:
    text = str(prompt_text or "").strip()
    lower = text.lower()
    constraints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = value.strip()
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        constraints.append(normalized)

    for match in re.findall(r'"([^"]+)"', text):
        add(f'Exact phrase required: "{match.strip()}"')

    gpu_patterns = [
        r"\bRTX\s*\d{3,4}(?:\s*Ti|\s*SUPER)?\b",
        r"\bGTX\s*\d{3,4}(?:\s*Ti|\s*SUPER)?\b",
        r"\bRX\s*\d{3,4}(?:\s*XT|\s*XTX)?\b",
        r"\bARC\s+[A-Z]?\d{3,4}\b",
    ]
    exact_models: list[str] = []
    for pattern in gpu_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            model = re.sub(r"\s+", " ", match).strip().upper()
            exact_models.append(model)
            add(f"Exact model required: {model}")

    if "rtx" in lower:
        add("Reject GTX results when RTX was requested.")
    if "gtx" in lower:
        add("Reject RTX results when GTX was requested.")

    numeric_model_tokens = re.findall(r"\b\d{3,4}\b", text)
    for token in numeric_model_tokens[:3]:
        add(f"Required model number: {token}")

    if re.search(r"\b(cheap|cheapest|deal|best deal|good deal|lowest price)\b", lower):
        add("Among valid exact matches, prefer the lowest price or best value deal.")

    return constraints


def build_browser_task(prompt_text: str, *, browser_context: str = "", conversation_context: str = "") -> str:
    parts = [
        "Continue from the current live Chrome state and selected tab. "
        "Use the current page unless I explicitly tell you to navigate elsewhere. "
        "Treat the latest request as the top priority if earlier context conflicts with it.",
        "If the current tab is a browser internal page like chrome://newtab or about:blank, do not try to type into it or click its controls. Open a real website first with a navigate or search action.",
        "If a popup or modal is covering the page, dismiss it first using an explicit close or no-thanks control. Do not click random background areas unless the backdrop is clearly intended to close it.",
    ]
    hard_constraints = _extract_hard_constraints(prompt_text)
    if hard_constraints:
        parts.append("Hard constraints:\n- " + "\n- ".join(hard_constraints))
    if browser_context.strip():
        parts.append("Current browser state:\n" + browser_context.strip())
    if conversation_context.strip():
        parts.append("Recent conversation:\n" + conversation_context.strip())
    parts.append("Current request:\n" + prompt_text.strip())
    return "\n\n".join(parts)


@dataclass
class SessionSnapshot:
    state: str = "idle"
    instruction: str = ""
    step: int = 0
    note: str = "Idle."
    last_result: str = ""
    model_name: str = ""
    last_action: str = ""
    run_id: str = ""
    artifacts_dir: str = ""
    latest_screenshot_path: str = ""
    screenshot_count: int = 0
    llm_input_used_screenshot: bool = False
    llm_input_screenshot_size: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _slugify_prompt(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:48] or "task"


def _summarize_action(model_output: Any) -> str:
    actions = getattr(model_output, "action", None)
    if not isinstance(actions, list) or not actions:
        return ""

    first_action = actions[0]
    if hasattr(first_action, "model_dump"):
        payload = first_action.model_dump(exclude_none=True)
    elif isinstance(first_action, dict):
        payload = dict(first_action)
    else:
        return ""

    if not isinstance(payload, dict) or not payload:
        return ""

    action_name, action_args = next(iter(payload.items()))
    if isinstance(action_args, dict):
        if isinstance(action_args.get("index"), int):
            return f"{action_name} #{action_args['index']}"
        if isinstance(action_args.get("url"), str):
            return f"{action_name} {action_args['url'][:80]}"
        if isinstance(action_args.get("text"), str):
            return f"{action_name} {action_args['text'][:80]}"
    return str(action_name)


def _format_screenshot_count(value: int) -> str:
    noun = "screenshot" if value == 1 else "screenshots"
    return f"{value} {noun}"


def _display_path(path: Path) -> str:
    try:
        home = Path.home().resolve()
        resolved = path.expanduser().resolve()
        if resolved == home or home in resolved.parents:
            return "~/" + str(resolved.relative_to(home))
    except Exception:
        pass
    return str(path)


def _step_page_identity(url: str | None, title: str | None) -> str:
    title_text = str(title or "").strip()
    url_text = str(url or "").strip()
    if title_text and url_text:
        return f"{title_text} ({url_text})"[:180]
    return (title_text or url_text or "current page")[:180]


def _step_signature(action_summary: str, url: str | None, title: str | None) -> tuple[str, str]:
    action_key = (action_summary or "unknown").strip().lower()
    page_key = _step_page_identity(url, title).lower()
    return action_key, page_key


def _normalize_page_url(url: str | None) -> str:
    return str(url or "").strip().split("#", 1)[0][:240]


def _screenshot_digest(screenshot_b64: str | None) -> str:
    if not isinstance(screenshot_b64, str):
        return ""
    normalized = screenshot_b64.strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("ascii", errors="ignore")).hexdigest()[:16]


def _page_state_signature(url: str | None, title: str | None, screenshot_b64: str | None) -> tuple[str, str, str]:
    return (
        _normalize_page_url(url).lower(),
        str(title or "").strip().lower()[:160],
        _screenshot_digest(screenshot_b64),
    )


class SessionController:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.browser = None
        self.current_task: asyncio.Task[None] | None = None
        self.stop_requested = False
        self.snapshot = SessionSnapshot()
        self._lock = asyncio.Lock()
        self._run_artifacts_dir: Path | None = None
        self._run_conversation_dir: Path | None = None
        self._step_screenshot_paths: list[str] = []
        self._last_step_signature: tuple[str, str] | None = None
        self._same_step_count = 0
        self._last_page_state_signature: tuple[str, str, str] | None = None
        self._same_page_state_count = 0
        self._loop_stop_reason: str | None = None

    async def ensure_browser(self) -> None:
        if self.browser is not None:
            return
        browser = build_browser(self.config, keep_alive=True)
        try:
            await asyncio.wait_for(browser.start(), timeout=BROWSER_START_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            logger.warning(
                "Timed out after %.1fs while attaching to Chrome over CDP.",
                BROWSER_START_TIMEOUT_SECONDS,
            )
            try:
                await asyncio.wait_for(browser.stop(), timeout=1.0)
            except Exception:
                pass
            raise RuntimeError(
                "Timed out while attaching to Chrome. Chromey could not start the browser session."
            ) from exc
        except Exception:
            try:
                await asyncio.wait_for(browser.stop(), timeout=1.0)
            except Exception:
                pass
            raise

        ensure_content_focus = getattr(browser, "ensure_content_focus", None)
        if callable(ensure_content_focus):
            try:
                await asyncio.wait_for(
                    ensure_content_focus(allow_bootstrap=False),
                    timeout=CONTENT_FOCUS_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "Timed out after %.1fs while restoring focus to an existing web tab after attach.",
                    CONTENT_FOCUS_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.warning("Failed to restore focus to an existing web tab after attach: %s", exc)

        self.browser = browser

    async def shutdown(self) -> None:
        await self.stop_current(wait_timeout=1.0)
        await self._reset_browser_connection()
        self.snapshot = SessionSnapshot()
        self._run_artifacts_dir = None
        self._run_conversation_dir = None
        self._step_screenshot_paths = []
        self._last_step_signature = None
        self._same_step_count = 0
        self._last_page_state_signature = None
        self._same_page_state_count = 0
        self._loop_stop_reason = None

    def _reset_tracking_state(self) -> None:
        self.stop_requested = False
        self._loop_stop_reason = None
        self._last_step_signature = None
        self._same_step_count = 0
        self._last_page_state_signature = None
        self._same_page_state_count = 0

    async def _reset_browser_connection(self) -> None:
        browser = self.browser
        self.browser = None
        if browser is None:
            return

        try:
            await asyncio.wait_for(browser.stop(), timeout=BROWSER_RESET_TIMEOUT_SECONDS)
            logger.info("Reset browser connection after stopping the previous task.")
        except TimeoutError:
            logger.warning(
                "Timed out after %.1fs while resetting the browser connection.",
                BROWSER_RESET_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Failed to reset the browser connection cleanly: %s", exc)

    async def handle_instruction(
        self,
        prompt_text: str,
        *,
        model_name: str | None = None,
        conversation_context: str = "",
        run_config: RuntimeConfig | None = None,
    ) -> str:
        normalized = prompt_text.strip()
        lower = normalized.lower()
        effective_config = run_config or self.config

        if lower in {"stop", "cancel", "stop task", "cancel task"}:
            return await self.stop_current()
        if lower in {"status", "progress", "what's happening", "what is happening"}:
            return self.status_text()

        async with self._lock:
            await self.ensure_browser()

            redirecting = self.current_task is not None and not self.current_task.done()
            if redirecting:
                await self._stop_active_task(wait_timeout=1.0)
                if self.browser is None:
                    await self.ensure_browser()
            await self._ensure_task_target_ready()

            self._reset_tracking_state()
            artifacts_dir = self._prepare_run_artifacts_dir(normalized)
            self.snapshot = SessionSnapshot(
                state="running",
                instruction=normalized,
                step=0,
                note="Preparing browser context.",
                last_result=self.snapshot.last_result,
                model_name=model_name or effective_config.model,
                run_id=artifacts_dir.name,
                artifacts_dir=str(artifacts_dir),
                llm_input_used_screenshot=False,
                llm_input_screenshot_size="",
            )
            self.current_task = asyncio.create_task(
                self._run_instruction(
                    prompt_text=normalized,
                    conversation_context=conversation_context,
                    model_name=model_name,
                    run_config=effective_config,
                ),
                name="chromey-session-run",
            )

        screenshots_dir = artifacts_dir / "screenshots"
        width, height = resolve_agent_performance_settings(effective_config).screenshot_size
        logger.info("Starting browser task: %s", normalized)
        logger.info("Artifacts directory: %s", _display_path(artifacts_dir))
        logger.info("Screenshots will be saved to %s", _display_path(screenshots_dir))
        logger.info(
            "Performance profile: %s. Vision mode: %s. Model-input screenshots will be resized to %sx%s.",
            effective_config.performance_profile,
            effective_config.use_vision,
            width,
            height,
        )
        if redirecting:
            return "Redirecting the task in Chrome."
        return "Working on it in Chrome."

    async def _ensure_task_target_ready(self) -> None:
        browser = self.browser
        if browser is None:
            return

        ensure_content_focus = getattr(browser, "ensure_content_focus", None)
        if not callable(ensure_content_focus):
            return

        try:
            await asyncio.wait_for(
                ensure_content_focus(allow_bootstrap=True),
                timeout=TASK_BOOTSTRAP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Timed out after %.1fs while preparing a real web page before starting the task.",
                TASK_BOOTSTRAP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Failed to prepare a usable web page before starting the task: %s", exc)

    async def stop_current(self, *, wait_timeout: float = 2.0) -> str:
        async with self._lock:
            if self.current_task is None or self.current_task.done():
                self._loop_stop_reason = None
                self.snapshot = replace(self.snapshot, state="idle", note="No browser task is running.")
                return "No browser task is currently running."

            self._loop_stop_reason = None
            await self._stop_active_task(wait_timeout=wait_timeout)
            self.snapshot = replace(self.snapshot, state="stopped", note="Stopped the browser task.")
            return "Stopped the browser task."

    async def reset_session(self, *, wait_timeout: float = 2.0) -> str:
        async with self._lock:
            if self.current_task is not None and not self.current_task.done():
                self._loop_stop_reason = None
                await self._stop_active_task(wait_timeout=wait_timeout)

            self._reset_tracking_state()
            self.snapshot = SessionSnapshot(note="Idle.")
            self._run_artifacts_dir = None
            self._run_conversation_dir = None
            self._step_screenshot_paths = []
            return "Started a new chat."

    def status_text(self) -> str:
        snapshot = self.snapshot
        if snapshot.state == "running":
            step_suffix = f" Step {snapshot.step}." if snapshot.step > 0 else ""
            return f"Browser task is running.{step_suffix} {snapshot.note}".strip()
        if snapshot.state == "completed":
            return f"{snapshot.note} {snapshot.last_result}".strip()
        if snapshot.state == "failed":
            return f"{snapshot.note} {snapshot.last_result}".strip()
        if snapshot.state == "stopped":
            return snapshot.note or "Browser task is stopped."
        return snapshot.note or "Idle."

    def snapshot_data(self) -> dict[str, object]:
        return self.snapshot.to_dict()

    async def _compose_task_prompt(self, prompt_text: str, *, conversation_context: str) -> str:
        browser_context = "Current browser state could not be read quickly. Start from the visible page and reassess from the first screenshot."
        try:
            browser_context = await asyncio.wait_for(
                self._describe_browser_context(),
                timeout=BROWSER_CONTEXT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Timed out after %.1fs while describing browser context before starting the task.",
                BROWSER_CONTEXT_TIMEOUT_SECONDS,
            )
        return build_browser_task(
            prompt_text,
            browser_context=browser_context,
            conversation_context=conversation_context,
        )

    async def _describe_browser_context(self) -> str:
        if self.browser is None:
            return "No browser session is connected yet."

        lines: list[str] = []
        try:
            ensure_content_focus = getattr(self.browser, "ensure_content_focus", None)
            if callable(ensure_content_focus):
                try:
                    await asyncio.wait_for(
                        ensure_content_focus(allow_bootstrap=False),
                        timeout=CONTENT_FOCUS_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    lines.append("Content-tab focus check timed out. Start from the currently visible page.")

            summary = await asyncio.wait_for(
                self.browser.get_browser_state_summary(include_screenshot=False, cached=True),
                timeout=2.0,
            )
            tabs = list(getattr(summary, "tabs", []) or [])
            visible_tabs = [
                tab
                for tab in tabs
                if not _is_extension_url(getattr(tab, "url", ""))
                and not _is_browser_internal_url(getattr(tab, "url", ""))
            ]
            primary_tab = None
            current_url = getattr(summary, "url", "") or ""
            current_title = getattr(summary, "title", "") or ""

            if current_url and not _is_extension_url(current_url) and not _is_browser_internal_url(current_url):
                primary_tab = type("PrimaryTab", (), {"title": current_title, "url": current_url})()
            elif visible_tabs:
                primary_tab = visible_tabs[-1]

            if primary_tab is not None:
                title = (getattr(primary_tab, "title", "") or "").strip()
                url = (getattr(primary_tab, "url", "") or "").strip()
                if title:
                    lines.append(f"Title: {title}")
                if url:
                    lines.append(f"URL: {url}")
                if _is_browser_internal_url(url):
                    lines.append("This is a browser internal page. Do not use its visible controls; open a real website first.")

            if visible_tabs:
                lines.append("Open tabs:")
                for tab in visible_tabs[:6]:
                    title = (getattr(tab, "title", "") or "").strip() or "(untitled)"
                    url = (getattr(tab, "url", "") or "").strip() or "about:blank"
                    lines.append(f"- {title}: {url}")
        except TimeoutError:
            lines.append("Browser state snapshot timed out. Start from the currently visible page.")
        except Exception:
            lines.append("No browser state is available yet.")

        return "\n".join(lines)

    async def _stop_active_task(self, *, wait_timeout: float) -> None:
        task = self.current_task
        if task is None:
            return

        self.stop_requested = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
        except TimeoutError:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=CANCEL_GRACE_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.warning(
                    "Task did not stop %.2fs after cancellation; detaching it and resetting the browser session.",
                    CANCEL_GRACE_TIMEOUT_SECONDS,
                )
                await self._reset_browser_connection()
        except asyncio.CancelledError:
            pass
        finally:
            if self.current_task is task:
                self.current_task = None
            self.stop_requested = False

    async def _should_stop(self) -> bool:
        return self.stop_requested

    def _track_repeated_step(self, *, action_summary: str, url: str, title: str) -> str:
        signature = _step_signature(action_summary, url, title)
        if signature == self._last_step_signature:
            self._same_step_count += 1
        else:
            self._last_step_signature = signature
            self._same_step_count = 1

        action_key = signature[0]
        repeat_limit = REPEATED_WAIT_LIMIT if action_key.startswith("wait") else REPEATED_ACTION_LIMIT
        if self._same_step_count < repeat_limit:
            return ""

        action_label = action_summary or "the same step"
        location = _step_page_identity(url, title)
        reason = f"Stopped to avoid repeating {action_label} on {location}."
        self._loop_stop_reason = reason
        self.stop_requested = True
        logger.warning(
            "Loop guard triggered after %d repeated steps: %s",
            self._same_step_count,
            reason,
        )
        return reason

    def _track_stalled_page_state(
        self,
        *,
        action_summary: str,
        url: str,
        title: str,
        screenshot_b64: str | None,
    ) -> str:
        signature = _page_state_signature(url, title, screenshot_b64)
        if not any(signature):
            self._last_page_state_signature = None
            self._same_page_state_count = 0
            return ""

        if signature == self._last_page_state_signature:
            self._same_page_state_count += 1
        else:
            self._last_page_state_signature = signature
            self._same_page_state_count = 1

        action_key = (action_summary or "").strip().lower()
        stall_limit = STALLED_WAIT_LIMIT if action_key.startswith("wait") else STALLED_PAGE_LIMIT
        if self._same_page_state_count < stall_limit:
            return ""

        location = _step_page_identity(url, title)
        reason = f"Stopped because the page did not visibly change after {self._same_page_state_count} steps on {location}."
        self._loop_stop_reason = reason
        self.stop_requested = True
        logger.warning(
            "No-progress guard triggered after %d unchanged page states: %s",
            self._same_page_state_count,
            reason,
        )
        return reason

    async def _register_step(self, browser_state_summary: Any, _model_output: Any, step_number: int) -> None:
        url = getattr(browser_state_summary, "url", "") or ""
        title = getattr(browser_state_summary, "title", "") or ""
        screenshot_b64 = getattr(browser_state_summary, "screenshot", None)
        where = title or url or "current page"
        action_summary = _summarize_action(_model_output)
        screenshot_path = await self._save_step_screenshot(browser_state_summary, step_number + 1)
        screenshot_count_text = _format_screenshot_count(len(self._step_screenshot_paths))
        llm_input_used_screenshot = bool(
            getattr(browser_state_summary, "_chromey_llm_input_includes_screenshot", False)
        )
        llm_input_screenshot_size = getattr(browser_state_summary, "_chromey_llm_input_screenshot_size", None)
        llm_input_screenshot_size_text = ""
        if isinstance(llm_input_screenshot_size, tuple) and len(llm_input_screenshot_size) == 2:
            llm_input_screenshot_size_text = (
                f"{llm_input_screenshot_size[0]}x{llm_input_screenshot_size[1]}"
            )
        note = f"Working on {where}."
        if action_summary:
            note = f"{action_summary} on {where}."
        note = f"{note} Saved {screenshot_count_text}."
        if llm_input_used_screenshot:
            if llm_input_screenshot_size_text:
                note = f"{note} Vision used {llm_input_screenshot_size_text}."
            else:
                note = f"{note} Vision used a screenshot."
        loop_reason = self._track_repeated_step(action_summary=action_summary, url=url, title=title)
        stall_reason = ""
        if not loop_reason:
            stall_reason = self._track_stalled_page_state(
                action_summary=action_summary,
                url=url,
                title=title,
                screenshot_b64=screenshot_b64,
            )
        if loop_reason or stall_reason:
            note = f"{note} {loop_reason or stall_reason}"
        self.snapshot = replace(
            self.snapshot,
            state="running",
            step=step_number + 1,
            note=note,
            last_action=action_summary,
            latest_screenshot_path=screenshot_path or self.snapshot.latest_screenshot_path,
            screenshot_count=len(self._step_screenshot_paths),
            llm_input_used_screenshot=llm_input_used_screenshot,
            llm_input_screenshot_size=llm_input_screenshot_size_text,
        )

    async def _register_done(self, history: Any) -> None:
        final_result = history.final_result() or ""
        success = bool(history.is_successful())
        final_screenshot_path = await self._save_runtime_screenshot("final")
        screenshot_count_text = _format_screenshot_count(len(self._step_screenshot_paths))
        if self._loop_stop_reason:
            self.snapshot = replace(
                self.snapshot,
                state="failed",
                note=f"Stopped to avoid a loop. Saved {screenshot_count_text}.",
                last_result=self._loop_stop_reason,
                latest_screenshot_path=final_screenshot_path or self.snapshot.latest_screenshot_path,
                screenshot_count=len(self._step_screenshot_paths),
            )
            return
        self.snapshot = replace(
            self.snapshot,
            state="completed" if success else "failed",
            note=(
                f"Completed the browser task. Saved {screenshot_count_text}."
                if success
                else f"Browser task stopped with errors. Saved {screenshot_count_text}."
            ),
            last_result=final_result,
            latest_screenshot_path=final_screenshot_path or self.snapshot.latest_screenshot_path,
            screenshot_count=len(self._step_screenshot_paths),
        )

    async def _run_instruction(
        self,
        *,
        prompt_text: str,
        conversation_context: str,
        model_name: str | None = None,
        run_config: RuntimeConfig | None = None,
    ) -> None:
        effective_config = run_config or self.config
        llm = build_llm(effective_config, model_override=model_name)
        client = llm.get_client()
        agent_state = AgentState()

        try:
            assert self.browser is not None
            task_prompt = await self._compose_task_prompt(prompt_text, conversation_context=conversation_context)
            self.snapshot = replace(
                self.snapshot,
                state="running",
                note="Starting the browser task.",
            )
            agent = build_agent(
                effective_config,
                task=task_prompt,
                browser=self.browser,
                llm=llm,
                register_new_step_callback=self._register_step,
                register_done_callback=self._register_done,
                register_should_stop_callback=self._should_stop,
                injected_agent_state=agent_state,
                save_conversation_path=str(self._run_conversation_dir) if self._run_conversation_dir else None,
            )
            history = await agent.run(max_steps=effective_config.max_steps)
            if self.snapshot.state == "running":
                final_screenshot_path = await self._save_runtime_screenshot("final")
                screenshot_count_text = _format_screenshot_count(len(self._step_screenshot_paths))
                if self._loop_stop_reason:
                    self.snapshot = replace(
                        self.snapshot,
                        state="failed",
                        note=f"Stopped to avoid a loop. Saved {screenshot_count_text}.",
                        last_result=self._loop_stop_reason,
                        latest_screenshot_path=final_screenshot_path or self.snapshot.latest_screenshot_path,
                        screenshot_count=len(self._step_screenshot_paths),
                    )
                    return
                self.snapshot = replace(
                    self.snapshot,
                    state="completed" if history.is_successful() else "failed",
                    note=(
                        f"Completed the browser task. Saved {screenshot_count_text}."
                        if history.is_successful()
                        else f"Browser task stopped with errors. Saved {screenshot_count_text}."
                    ),
                    last_result=history.final_result() or "",
                    latest_screenshot_path=final_screenshot_path or self.snapshot.latest_screenshot_path,
                    screenshot_count=len(self._step_screenshot_paths),
                )
        except asyncio.CancelledError:
            stopped_screenshot_path = await self._save_runtime_screenshot("stopped")
            screenshot_count_text = _format_screenshot_count(len(self._step_screenshot_paths))
            self.snapshot = replace(
                self.snapshot,
                state="stopped",
                note=f"Stopped the browser task. Saved {screenshot_count_text}.",
                latest_screenshot_path=stopped_screenshot_path or self.snapshot.latest_screenshot_path,
                screenshot_count=len(self._step_screenshot_paths),
            )
            raise
        except Exception as exc:
            failed_screenshot_path = await self._save_runtime_screenshot("failed")
            screenshot_count_text = _format_screenshot_count(len(self._step_screenshot_paths))
            self.snapshot = replace(
                self.snapshot,
                state="failed",
                note=f"Browser task failed. Saved {screenshot_count_text}.",
                last_result=str(exc),
                latest_screenshot_path=failed_screenshot_path or self.snapshot.latest_screenshot_path,
                screenshot_count=len(self._step_screenshot_paths),
            )
        finally:
            await close_llm_client(client)
            if self.current_task is not None and self.current_task.done():
                self.current_task = None

    def _prepare_run_artifacts_dir(self, prompt_text: str) -> Path:
        root = Path(self.config.artifacts_dir or DEFAULT_ARTIFACTS_DIR).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slugify_prompt(prompt_text)}"
        run_dir = root / run_id
        suffix = 2
        while run_dir.exists():
            run_dir = root / f"{run_id}-{suffix}"
            suffix += 1
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "screenshots").mkdir(exist_ok=True)
        conversation_dir = run_dir / "conversation"
        conversation_dir.mkdir(exist_ok=True)
        self._run_artifacts_dir = run_dir
        self._run_conversation_dir = conversation_dir
        self._step_screenshot_paths = []
        return run_dir

    async def _save_step_screenshot(self, browser_state_summary: Any, step_number: int) -> str:
        screenshot_dir = self._run_artifacts_dir / "screenshots" if self._run_artifacts_dir else None
        if screenshot_dir is None:
            return ""
        screenshot_path = screenshot_dir / f"step-{step_number:03d}.png"

        screenshot_b64 = getattr(browser_state_summary, "screenshot", None)
        if isinstance(screenshot_b64, str) and screenshot_b64.strip():
            try:
                screenshot_path.write_bytes(base64.b64decode(screenshot_b64))
                resolved = str(screenshot_path)
                self._step_screenshot_paths.append(resolved)
                logger.info(
                    "Captured browser-state screenshot and saved step %03d to %s",
                    step_number,
                    _display_path(screenshot_path),
                )
                return resolved
            except Exception:
                pass

        return await self._save_runtime_screenshot(f"step-{step_number:03d}")

    async def _save_runtime_screenshot(self, stem: str) -> str:
        screenshot_dir = self._run_artifacts_dir / "screenshots" if self._run_artifacts_dir else None
        if screenshot_dir is None or self.browser is None:
            return ""

        screenshot_path = screenshot_dir / f"{stem}.png"
        try:
            await self.browser.take_screenshot(path=str(screenshot_path))
        except Exception:
            return ""

        resolved = str(screenshot_path)
        if resolved not in self._step_screenshot_paths:
            self._step_screenshot_paths.append(resolved)
        logger.info("Saved runtime screenshot to %s", _display_path(screenshot_path))
        return resolved
