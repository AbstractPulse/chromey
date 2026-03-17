from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from browser_use import Agent, Browser
from browser_use.llm.base import BaseChatModel

from chromey.browser_use_instrumentation import install_browser_use_logging_hooks, instrument_browser
from chromey.chrome import probe_cdp_url
from chromey.config import RuntimeConfig
from chromey.lmstudio import resolve_llm_timeout

COMPACT_INCLUDE_ATTRIBUTES = [
    "aria-label",
    "placeholder",
    "title",
    "value",
    "type",
    "role",
    "alt",
]

BALANCED_OUTPUT_RULES = """
Critical output rules:
- Return raw JSON only. Do not wrap JSON in markdown fences.
- Return only the required JSON keys. Do not add commentary.
- Keep memory very short.
- The action array must contain exactly one action.
- Never return an empty action array.
- If the task is complete, the single action must be done with success=true.
- If the page is still loading, return a JSON wait action like {"evaluation_previous_goal":"Page still loading","memory":"loading","next_goal":"Wait for the page to finish loading","action":[{"wait":{"seconds":3}}]}.
- If you want to open a site, return a JSON navigate action, not prose.
- Never interact with browser-internal pages like chrome://newtab, chrome://new-tab-page, or about:blank using element indexes. Open a real website first with navigate or search.
- If a popup, modal, cookie banner, newsletter signup, region selector, or sign-in wall blocks the page, dismiss it before continuing the main task.
- Prefer explicit dismiss controls like Close, X, No thanks, Skip, Continue without joining, or Maybe later over clicking arbitrary blank areas.
- Only click outside a popup if the screenshot clearly shows a dismissable backdrop and there is no better close control.
- If one dismiss attempt fails, reassess from the current screenshot instead of clicking the same area again. If needed, try a different visible close control or send Escape.
- After dismissing a popup, verify the overlay disappeared before resuming the underlying task.
- For click and input actions, use numeric browser-use element indexes from the current browser state.
- Use the highlighted element indexes and screenshot to verify you are choosing the intended control before clicking.
- Prefer waiting briefly and reassessing over clicking a likely-wrong element.
- If several elements are similar, choose the one whose nearby text best matches the current goal.
- After navigation or submit actions, verify that the page actually changed before continuing.
- After typing into a search box on Google or another search page, prefer send_keys with {"keys":"Enter"} to submit the query.
- If the typed query is already present in the focused search field and search results have not loaded yet, use send_keys {"keys":"Enter"} instead of clicking around the page.
- Do not click top navigation tabs like Images, Videos, News, or Shopping unless the user explicitly asked for that tab.
- Treat Google tabs like Images, Videos, News, and Shopping as result filters, not as search submit buttons.
- For shopping or product-finding tasks, preserve the exact requested product family, model number, and key identifiers. Do not substitute close alternatives.
- Never treat RTX and GTX as equivalent. Never treat different model numbers like 3060 and 4060 as equivalent.
- Before deciding a result is valid, verify the visible product title or specs contain the requested identifiers. If they do not, reject the result and keep looking.
- Do not repeat the same click, input, search, or wait on the same page more than twice without a visible page change.
- If two similar attempts do not change the page, reassess from the current screenshot and choose a different visible target or approach.
- If the URL, title, and visible page state remain unchanged across several steps, stop retrying the same area and choose a materially different action.
- To scroll to visible text, use the find_text action with {"text":"..."}.
""".strip()

FAST_OUTPUT_RULES = """
Critical rules:
- Return raw JSON only, with exactly one action.
- Keep memory short and concrete.
- Use the screenshot as ground truth before clicking or typing.
- If the page is internal Chrome UI, open a real website first.
- If a popup blocks the page, dismiss it using a visible close control before continuing.
- Prefer explicit controls over clicking empty space.
- Use only numeric browser-use indexes from the current state.
- After typing into a search box on Google or another search page, prefer send_keys with {"keys":"Enter"} to submit the query.
- If the typed query is already present in the focused search field and search results have not loaded yet, use send_keys {"keys":"Enter"} instead of clicking around the page.
- Do not click top navigation tabs like Images, Videos, News, or Shopping unless the user explicitly asked for that tab.
- Treat Google tabs like Images, Videos, News, and Shopping as result filters, not as search submit buttons.
- If two similar attempts do not change the page, choose a different action.
- For shopping tasks, preserve exact product identifiers. RTX is not GTX, and 3060 is not 4060.
- Reject results whose visible title or specs do not contain the requested identifiers.
""".strip()


@dataclass(frozen=True)
class AgentPerformanceSettings:
    screenshot_size: tuple[int, int]
    max_clickable_elements_length: int
    vision_detail_level: str
    max_history_items: int | None
    include_attributes: list[str] | None
    flash_mode: bool
    output_rules: str


def resolve_agent_performance_settings(config: RuntimeConfig) -> AgentPerformanceSettings:
    override_size = None
    if isinstance(getattr(config, "screenshot_width", None), int) and isinstance(getattr(config, "screenshot_height", None), int):
        override_size = (config.screenshot_width, config.screenshot_height)

    if getattr(config, "performance_profile", "balanced") == "fast":
        return AgentPerformanceSettings(
            screenshot_size=override_size or (640, 400),
            max_clickable_elements_length=2500,
            vision_detail_level="low",
            max_history_items=6,
            include_attributes=COMPACT_INCLUDE_ATTRIBUTES,
            flash_mode=True,
            output_rules=FAST_OUTPUT_RULES,
        )

    return AgentPerformanceSettings(
        screenshot_size=override_size or (1280, 800),
        max_clickable_elements_length=12000,
        vision_detail_level="high",
        max_history_items=None,
        include_attributes=None,
        flash_mode=False,
        output_rules=BALANCED_OUTPUT_RULES,
    )


def build_browser(config: RuntimeConfig, *, keep_alive: bool) -> Browser:
    install_browser_use_logging_hooks()

    if not config.cdp_url:
        raise RuntimeError(
            "Chromey needs a Chrome CDP URL. Start Chrome with --remote-debugging-port=9222 or use --launch-browser."
        )
    if not probe_cdp_url(config.cdp_url):
        raise RuntimeError(
            f"Chrome is not reachable at {config.cdp_url}. Start Chrome with --remote-debugging-port=9222 or use --launch-browser."
        )

    browser = Browser(
        cdp_url=config.cdp_url,
        is_local=True,
        keep_alive=keep_alive,
        minimum_wait_page_load_time=0.20,
        wait_for_network_idle_page_load_time=0.45,
        wait_between_actions=0.12,
        highlight_elements=True,
        # browser-use treats DOM overlays and interaction highlights as conflicting modes.
        # Keep the interaction highlight so the chosen element visibly flashes in live Chrome.
        dom_highlight_elements=False,
        filter_highlight_ids=False,
        cross_origin_iframes=True,
    )
    return instrument_browser(browser)


def build_agent(
    config: RuntimeConfig,
    *,
    task: str,
    browser: Browser,
    llm: BaseChatModel,
    register_new_step_callback: Any | None = None,
    register_done_callback: Any | None = None,
    register_should_stop_callback: Any | None = None,
    injected_agent_state: Any | None = None,
    save_conversation_path: str | None = None,
) -> Agent:
    install_browser_use_logging_hooks()
    llm_timeout = resolve_llm_timeout(config)
    performance = resolve_agent_performance_settings(config)

    return Agent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=config.use_vision,
        use_judge=False,
        enable_planning=False,
        use_thinking=False,
        flash_mode=performance.flash_mode,
        extend_system_message=performance.output_rules,
        message_compaction=True,
        save_conversation_path=save_conversation_path,
        max_failures=config.max_failures or 2,
        max_actions_per_step=config.max_actions_per_step or 1,
        max_history_items=performance.max_history_items,
        max_clickable_elements_length=performance.max_clickable_elements_length,
        include_attributes=performance.include_attributes,
        llm_screenshot_size=performance.screenshot_size,
        vision_detail_level=performance.vision_detail_level,
        include_recent_events=False,
        llm_timeout=llm_timeout,
        step_timeout=config.step_timeout,
        register_new_step_callback=register_new_step_callback,
        register_done_callback=register_done_callback,
        register_should_stop_callback=register_should_stop_callback,
        injected_agent_state=injected_agent_state,
        directly_open_url=False,
    )
