from __future__ import annotations

import asyncio
import base64
import logging
from functools import wraps
from io import BytesIO
from types import MethodType
from typing import Any

logger = logging.getLogger(__name__)

FOCUS_SWITCH_TIMEOUT_SECONDS = 1.5
BOOTSTRAP_PAGE_URL = "https://www.google.com/"
BOOTSTRAP_NAVIGATION_TIMEOUT_SECONDS = 8.0
CONTENT_PREP_TIMEOUT_SECONDS = 8.5
SCREENSHOT_CAPTURE_TIMEOUT_SECONDS = 8.0


def _is_extension_url(url: str | None) -> bool:
    return str(url or "").startswith("chrome-extension://")


def _is_browser_internal_url(url: str | None) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    if value.startswith("chrome-extension://") or value.startswith("devtools://"):
        return False
    if value in {"about:blank", "chrome://newtab/", "chrome://newtab", "chrome://new-tab-page/", "chrome://new-tab-page"}:
        return True
    return value.startswith("chrome://")


def _is_usable_content_url(url: str | None) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    if value.startswith(("http://", "https://", "file://")):
        return True
    return False


def _pick_content_target(browser: Any) -> Any | None:
    session_manager = getattr(browser, "session_manager", None)
    if session_manager is None:
        return None

    focused_target = browser.get_focused_target() if hasattr(browser, "get_focused_target") else None
    focused_url = getattr(focused_target, "url", None) if focused_target is not None else None
    if focused_target is not None and not _is_browser_internal_url(focused_url) and _is_usable_content_url(focused_url):
        return focused_target

    page_targets = list(session_manager.get_all_page_targets() or [])
    for target in reversed(page_targets):
        target_url = getattr(target, "url", None)
        if _is_usable_content_url(target_url):
            return target

    return None


async def _bootstrap_content_page(browser: Any) -> Any | None:
    navigate_to = getattr(browser, "navigate_to", None)
    if not callable(navigate_to):
        return None

    if getattr(browser, "_chromey_bootstrap_inflight", False):
        return None

    object.__setattr__(browser, "_chromey_bootstrap_inflight", True)
    try:
        logger.info(
            "No usable web page is open. Opening %s so Chromey can start from a real page.",
            BOOTSTRAP_PAGE_URL,
        )
        await asyncio.wait_for(
            navigate_to(BOOTSTRAP_PAGE_URL, new_tab=True),
            timeout=BOOTSTRAP_NAVIGATION_TIMEOUT_SECONDS,
        )
        await asyncio.sleep(0.4)
        return _pick_content_target(browser)
    except TimeoutError:
        logger.warning(
            "Timed out after %.1fs while opening a real web page from Chrome's internal UI.",
            BOOTSTRAP_NAVIGATION_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:
        logger.warning("Failed to open a real web page from Chrome's internal UI: %s", exc)
        return None
    finally:
        object.__setattr__(browser, "_chromey_bootstrap_inflight", False)


async def ensure_content_focus(browser: Any, *, allow_bootstrap: bool = False) -> Any | None:
    target = _pick_content_target(browser)
    if target is None and allow_bootstrap:
        target = await _bootstrap_content_page(browser)
        if target is None:
            return None
    elif target is None:
        return None

    focused_target = browser.get_focused_target() if hasattr(browser, "get_focused_target") else None
    focused_target_id = getattr(focused_target, "target_id", None)
    target_id = getattr(target, "target_id", None)

    if target_id is None or focused_target_id == target_id:
        return target

    try:
        from browser_use.browser.events import SwitchTabEvent

        event = browser.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
        await asyncio.wait_for(event, timeout=FOCUS_SWITCH_TIMEOUT_SECONDS)
        await asyncio.wait_for(
            event.event_result(raise_if_any=False, raise_if_none=False),
            timeout=FOCUS_SWITCH_TIMEOUT_SECONDS,
        )
        logger.info("Shifted agent focus to content tab: %s", getattr(target, "url", "") or target_id)
    except TimeoutError:
        logger.warning("Timed out while shifting agent focus to content tab: %s", getattr(target, "url", "") or target_id)
    except Exception as exc:
        logger.warning("Failed to shift agent focus away from extension UI: %s", exc)

    return target


def _image_size_from_base64(screenshot_b64: str) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(BytesIO(base64.b64decode(screenshot_b64))) as image:
            return image.size
    except Exception:
        return None


def _image_size_from_bytes(screenshot_bytes: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(BytesIO(screenshot_bytes)) as image:
            return image.size
    except Exception:
        return None


def _is_placeholder_screenshot(screenshot_b64: str | None) -> bool:
    if not isinstance(screenshot_b64, str) or not screenshot_b64.strip():
        return True

    try:
        from browser_use.browser.views import PLACEHOLDER_4PX_SCREENSHOT

        if screenshot_b64 == PLACEHOLDER_4PX_SCREENSHOT:
            return True
    except Exception:
        pass

    return _image_size_from_base64(screenshot_b64) == (4, 4)


def _filter_summary_tabs(summary: Any) -> None:
    tabs = list(getattr(summary, "tabs", []) or [])
    if not tabs:
        return
    content_tabs = [tab for tab in tabs if _is_usable_content_url(getattr(tab, "url", None))]
    if content_tabs:
        setattr(summary, "tabs", content_tabs)


def _summary_has_usable_content(summary: Any) -> bool:
    if _is_usable_content_url(getattr(summary, "url", None)):
        return True
    return any(_is_usable_content_url(getattr(tab, "url", None)) for tab in list(getattr(summary, "tabs", []) or []))


def install_browser_use_logging_hooks() -> None:
    from browser_use.agent.message_manager.service import MessageManager
    from browser_use.agent.prompts import AgentMessagePrompt

    if getattr(MessageManager, "_chromey_screenshot_logging_installed", False):
        return

    original_create_state_messages = MessageManager.create_state_messages
    original_resize_screenshot = AgentMessagePrompt._resize_screenshot

    @wraps(original_create_state_messages)
    def wrapped_create_state_messages(
        self,
        browser_state_summary,
        model_output=None,
        result=None,
        step_info=None,
        use_vision: bool | str = True,
        page_filtered_actions=None,
        sensitive_data=None,
        available_file_paths=None,
        unavailable_skills_info=None,
        plan_description=None,
        skip_state_update: bool = False,
    ) -> None:
        include_screenshot_requested = False
        if result:
            for action_result in result:
                metadata = getattr(action_result, "metadata", None)
                if isinstance(metadata, dict) and metadata.get("include_screenshot"):
                    include_screenshot_requested = True
                    break

        include_screenshot = bool(use_vision is True or (use_vision == "auto" and include_screenshot_requested))
        screenshot_b64 = getattr(browser_state_summary, "screenshot", None)
        dimensions = _image_size_from_base64(screenshot_b64) if isinstance(screenshot_b64, str) and screenshot_b64.strip() else None
        screenshot_sent = bool(include_screenshot and isinstance(screenshot_b64, str) and screenshot_b64.strip())

        setattr(browser_state_summary, "_chromey_llm_input_includes_screenshot", screenshot_sent)
        setattr(browser_state_summary, "_chromey_llm_input_screenshot_size", dimensions)

        if include_screenshot:
            if isinstance(screenshot_b64, str) and screenshot_b64.strip():
                if dimensions:
                    logger.info(
                        "Browser screenshot ready for model input at %sx%s.",
                        dimensions[0],
                        dimensions[1],
                    )
                else:
                    logger.info(
                        "Browser screenshot ready for model input (%d base64 chars).",
                        len(screenshot_b64),
                    )

                resize_target = getattr(self, "llm_screenshot_size", None)
                if isinstance(resize_target, tuple) and len(resize_target) == 2:
                    logger.info(
                        "Sending screenshot to the model with vision enabled (resize target %sx%s).",
                        resize_target[0],
                        resize_target[1],
                    )
                else:
                    logger.info("Sending screenshot to the model with vision enabled.")
            else:
                logger.warning(
                    "Vision is enabled for this step but browser state has no screenshot. The model will receive text only."
                )

        return original_create_state_messages(
            self,
            browser_state_summary,
            model_output=model_output,
            result=result,
            step_info=step_info,
            use_vision=use_vision,
            page_filtered_actions=page_filtered_actions,
            sensitive_data=sensitive_data,
            available_file_paths=available_file_paths,
            unavailable_skills_info=unavailable_skills_info,
            plan_description=plan_description,
            skip_state_update=skip_state_update,
        )

    @wraps(original_resize_screenshot)
    def wrapped_resize_screenshot(self, screenshot_b64: str) -> str:
        target = getattr(self, "llm_screenshot_size", None)
        source_size = _image_size_from_base64(screenshot_b64)

        if isinstance(target, tuple) and len(target) == 2 and source_size is not None:
            if source_size != target:
                logger.info(
                    "Resizing screenshot from %sx%s to %sx%s for LLM.",
                    source_size[0],
                    source_size[1],
                    target[0],
                    target[1],
                )
            else:
                logger.info(
                    "Screenshot already matches LLM target size %sx%s.",
                    target[0],
                    target[1],
                )

        return original_resize_screenshot(self, screenshot_b64)

    MessageManager.create_state_messages = wrapped_create_state_messages
    AgentMessagePrompt._resize_screenshot = wrapped_resize_screenshot
    MessageManager._chromey_screenshot_logging_installed = True


def instrument_browser(browser: Any) -> Any:
    if getattr(browser, "_chromey_screenshot_instrumented", False):
        return browser

    original_get_browser_state_summary = browser.get_browser_state_summary
    original_take_screenshot = browser.take_screenshot

    @wraps(original_get_browser_state_summary)
    async def wrapped_get_browser_state_summary(_self, *args, **kwargs):
        try:
            await asyncio.wait_for(ensure_content_focus(_self, allow_bootstrap=False), timeout=CONTENT_PREP_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Timed out while preparing content focus before reading browser state.")

        include_screenshot = True
        if "include_screenshot" in kwargs:
            include_screenshot = bool(kwargs["include_screenshot"])
        elif args:
            include_screenshot = bool(args[0])

        summary = await original_get_browser_state_summary(*args, **kwargs)
        _filter_summary_tabs(summary)
        if not _summary_has_usable_content(summary):
            try:
                await asyncio.wait_for(ensure_content_focus(_self, allow_bootstrap=True), timeout=CONTENT_PREP_TIMEOUT_SECONDS)
                summary = await original_get_browser_state_summary(*args, **kwargs)
                _filter_summary_tabs(summary)
            except TimeoutError:
                logger.warning("Timed out while bootstrapping a real web page before reading browser state.")
            except Exception as exc:
                logger.warning("Failed to bootstrap a real web page before reading browser state: %s", exc)
        if not include_screenshot:
            return summary

        screenshot_b64 = getattr(summary, "screenshot", None)
        if not _is_placeholder_screenshot(screenshot_b64):
            return summary

        logger.warning(
            "Browser state arrived without a usable screenshot. Capturing a fallback screenshot directly from Chrome."
        )
        try:
            screenshot_bytes = await asyncio.wait_for(
                original_take_screenshot(),
                timeout=SCREENSHOT_CAPTURE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Timed out after %.1fs while capturing a fallback screenshot directly from Chrome.",
                SCREENSHOT_CAPTURE_TIMEOUT_SECONDS,
            )
            return summary
        except Exception as exc:
            logger.warning("Fallback screenshot capture failed: %s", exc)
            return summary

        encoded = base64.b64encode(screenshot_bytes).decode("utf-8")
        setattr(summary, "screenshot", encoded)

        dimensions = _image_size_from_bytes(screenshot_bytes)
        if dimensions:
            logger.info(
                "Attached fallback screenshot to browser state at %sx%s (%d bytes).",
                dimensions[0],
                dimensions[1],
                len(screenshot_bytes),
            )
        else:
            logger.info(
                "Attached fallback screenshot to browser state (%d bytes).",
                len(screenshot_bytes),
            )
        return summary

    @wraps(original_take_screenshot)
    async def wrapped_take_screenshot(_self, *args, **kwargs):
        try:
            await asyncio.wait_for(ensure_content_focus(_self, allow_bootstrap=False), timeout=CONTENT_PREP_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Timed out while preparing content focus before taking a screenshot.")
        return await original_take_screenshot(*args, **kwargs)

    object.__setattr__(browser, "get_browser_state_summary", MethodType(wrapped_get_browser_state_summary, browser))
    object.__setattr__(browser, "take_screenshot", MethodType(wrapped_take_screenshot, browser))
    object.__setattr__(browser, "ensure_content_focus", MethodType(ensure_content_focus, browser))
    object.__setattr__(browser, "_chromey_screenshot_instrumented", True)
    return browser
