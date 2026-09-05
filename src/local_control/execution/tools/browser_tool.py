"""Browser automation tool adapter using Playwright."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from local_control.config.settings import Settings
from local_control.core.actions import (
    Action,
    BrowserBackAction,
    BrowserClickAction,
    BrowserDownloadAction,
    BrowserNavigateAction,
    BrowserReadAction,
    BrowserSnapshotAction,
    BrowserTabsAction,
    BrowserTypeAction,
)
from local_control.core.types import (
    ActionResult,
    BrowserObservation,
    BrowserTabInfo,
    ErrorInfo,
)
from local_control.execution.tools.base import ExecutionContext, Tool

logger = structlog.get_logger(__name__)


class BrowserTool(Tool):
    """Executes browser actions through Playwright with dedicated persistent context."""

    def __init__(
        self,
        profile_dir: str | Path | None = None,
        headless: bool | None = None,
        channel: str | None = None,
        download_dir: str | Path | None = None,
        snapshot_max_nodes: int | None = None,
    ) -> None:
        self._profile_dir = Path(profile_dir) if profile_dir else None
        self._headless = headless
        self._channel = channel
        self._download_dir = Path(download_dir) if download_dir else None
        self._snapshot_max_nodes = snapshot_max_nodes

        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._nav_id: int = 0
        self._snapshot_nav_id: int = 0
        self._refs_valid: bool = False
        self._refs: dict[str, dict[str, Any]] = {}
        self._last_snapshot: str = ""

    @property
    def handles(self) -> frozenset[str]:
        return frozenset(
            {
                "browser_navigate",
                "browser_click",
                "browser_type",
                "browser_read",
                "browser_snapshot",
                "browser_back",
                "browser_tabs",
                "browser_download",
            }
        )

    @property
    def is_active(self) -> bool:
        """Return True if browser context is running and has open pages."""
        return self._context is not None and self._page is not None and not self._page.is_closed()

    async def ensure_browser(self, settings: Settings | None = None) -> Page:
        """Ensure browser context and active page are running."""
        if self._context is not None and self._page is not None and not self._page.is_closed():
            return self._page

        # Determine user data directory
        if self._profile_dir:
            profile_path = self._profile_dir
        elif settings and settings.browser.profile_dir:
            profile_path = Path(settings.browser.profile_dir)
        else:
            local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / ".local-control"))
            profile_path = Path(local_appdata) / "local-control" / "browser-profile"

        profile_path = profile_path.expanduser().resolve()
        profile_path.mkdir(parents=True, exist_ok=True)

        # Determine download directory
        if self._download_dir:
            dl_path = self._download_dir
        elif settings and settings.browser.download_dir:
            dl_path = Path(settings.browser.download_dir)
        else:
            dl_path = Path.home() / "Downloads" / "local-control"

        dl_path = dl_path.expanduser().resolve()
        dl_path.mkdir(parents=True, exist_ok=True)

        # Determine headless
        headless = self._headless
        if headless is None:
            headless = settings.browser.headless if settings else False

        # Determine channel
        channel = self._channel
        if channel is None and settings and settings.browser.channel:
            channel = settings.browser.channel

        if not self._playwright:
            self._playwright = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_path),
            "headless": headless,
            "downloads_path": str(dl_path),
            "accept_downloads": True,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if channel:
            launch_kwargs["channel"] = channel

        logger.info(
            "browser_tool.launching",
            profile=str(profile_path),
            headless=headless,
            channel=channel,
        )
        self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        self._attach_page_listeners(self._page)
        return self._page

    def _attach_page_listeners(self, page: Page) -> None:
        """Attach navigation listener to invalidate refs when page navigates."""

        def on_framenavigated(frame: Any) -> None:
            if frame == page.main_frame:
                self._nav_id += 1
                self._refs_valid = False
                logger.debug("browser_tool.navigated", url=page.url, nav_id=self._nav_id)

        page.on("framenavigated", on_framenavigated)

    async def close(self) -> None:
        """Close browser context and stop Playwright."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("browser_tool.context_close_error", error=str(e))
            self._context = None
            self._page = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("browser_tool.playwright_stop_error", error=str(e))
            self._playwright = None

        self._refs.clear()
        self._refs_valid = False
        self._last_snapshot = ""

    async def execute(self, action: Action, ctx: ExecutionContext) -> ActionResult:
        """Execute browser action within runtime context."""
        started_at = datetime.now(UTC)
        start_mono = time.monotonic()

        try:
            if isinstance(action, BrowserNavigateAction):
                return await self._execute_navigate(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserClickAction):
                return await self._execute_click(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserTypeAction):
                return await self._execute_type(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserReadAction):
                return await self._execute_read(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserSnapshotAction):
                return await self._execute_snapshot(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserBackAction):
                return await self._execute_back(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserTabsAction):
                return await self._execute_tabs(action, ctx, started_at, start_mono)
            elif isinstance(action, BrowserDownloadAction):
                return await self._execute_download(action, ctx, started_at, start_mono)
            else:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="UNSUPPORTED_ACTION",
                        message=f"BrowserTool does not handle '{action.type}'",
                    ),
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            logger.error("browser_tool.execute_failed", action_type=action.type, error=str(e))
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=duration_ms,
                error=ErrorInfo(
                    code="BROWSER_EXECUTION_ERROR",
                    message=f"Browser execution error: {e}",
                ),
            )

    async def _execute_navigate(
        self,
        action: BrowserNavigateAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)
        await page.goto(action.url, wait_until="load", timeout=30000)
        self._nav_id += 1
        self._refs_valid = False
        self._refs.clear()

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        title = await page.title()
        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=f"Navigated to {page.url} ('{title}')",
        )

    async def _execute_click(
        self,
        action: BrowserClickAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)

        if action.ref:
            if (
                not self._refs_valid
                or self._snapshot_nav_id != self._nav_id
                or action.ref not in self._refs
            ):
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="browser_stale_ref",
                        message=f"Reference '{action.ref}' is stale. Capture a new snapshot.",
                    ),
                )
            loc = page.locator(f"[data-lc-ref='{action.ref}']")
            if await loc.count() == 0:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="browser_stale_ref",
                        message=f"Element for reference '{action.ref}' is no longer in DOM. Capture a new snapshot.",
                    ),
                )
        elif action.selector:
            loc = page.locator(action.selector)
        else:
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=int((time.monotonic() - start_mono) * 1000),
                error=ErrorInfo(
                    code="INVALID_PARAMS",
                    message="Neither ref nor selector provided for browser_click",
                ),
            )

        await loc.first.click(timeout=10000)

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        target = action.ref or action.selector
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=f"Clicked element {target}",
        )

    async def _execute_type(
        self,
        action: BrowserTypeAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)

        if action.ref:
            if (
                not self._refs_valid
                or self._snapshot_nav_id != self._nav_id
                or action.ref not in self._refs
            ):
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="browser_stale_ref",
                        message=f"Reference '{action.ref}' is stale. Capture a new snapshot.",
                    ),
                )
            loc = page.locator(f"[data-lc-ref='{action.ref}']")
            if await loc.count() == 0:
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="browser_stale_ref",
                        message=f"Element for reference '{action.ref}' is no longer in DOM. Capture a new snapshot.",
                    ),
                )
            tag = self._refs.get(action.ref, {}).get("tag", "").lower()
        elif action.selector:
            loc = page.locator(action.selector)
            try:
                tag = (await loc.first.evaluate("el => el.tagName.toLowerCase()")) or ""
            except Exception:
                tag = ""
        else:
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=int((time.monotonic() - start_mono) * 1000),
                error=ErrorInfo(
                    code="INVALID_PARAMS",
                    message="Neither ref nor selector provided for browser_type",
                ),
            )

        if tag == "select":
            try:
                await loc.first.select_option(label=action.text, timeout=5000)
            except Exception:
                await loc.first.select_option(value=action.text, timeout=5000)
        else:
            await loc.first.fill(action.text, timeout=10000)
            if action.submit:
                await loc.first.press("Enter")

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        target = action.ref or action.selector
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=f"Typed '{action.text}' into {target}",
        )

    async def _execute_read(
        self,
        action: BrowserReadAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)

        if action.selector:
            text = await page.locator(action.selector).first.inner_text(timeout=10000)
        else:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")

        if action.max_chars and len(text) > action.max_chars:
            text = text[: action.max_chars]

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=text,
        )

    async def _execute_snapshot(
        self,
        action: BrowserSnapshotAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)
        max_nodes = self._snapshot_max_nodes or (
            ctx.settings.browser.snapshot_max_nodes if ctx.settings else 400
        )
        snapshot_text = await self._take_snapshot(page, max_nodes=max_nodes)

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=snapshot_text,
        )

    async def _execute_back(
        self,
        action: BrowserBackAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)
        await page.go_back(wait_until="load", timeout=15000)
        self._nav_id += 1
        self._refs_valid = False
        self._refs.clear()

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=f"Navigated back to {page.url}",
        )

    async def _execute_tabs(
        self,
        action: BrowserTabsAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        await self.ensure_browser(ctx.settings)
        assert self._context is not None
        pages = self._context.pages

        if action.op == "list":
            tab_lines = []
            for i, p in enumerate(pages):
                title = await p.title() if not p.is_closed() else ""
                active_str = " (ACTIVE)" if p == self._page else ""
                tab_lines.append(f"[{i}] '{title}' <{p.url}>{active_str}")
            output = "\n".join(tab_lines)
        elif action.op == "new":
            new_p = await self._context.new_page()
            self._page = new_p
            self._attach_page_listeners(new_p)
            self._nav_id += 1
            self._refs_valid = False
            output = f"Opened new tab [{len(self._context.pages) - 1}]"
        elif action.op == "switch":
            if action.index is None or action.index < 0 or action.index >= len(pages):
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="INVALID_TAB_INDEX",
                        message=f"Tab index {action.index} out of range [0, {len(pages) - 1}]",
                    ),
                )
            self._page = pages[action.index]
            await self._page.bring_to_front()
            self._refs_valid = False
            output = f"Switched to tab [{action.index}] {self._page.url}"
        elif action.op == "close":
            target_idx = action.index if action.index is not None else pages.index(self._page)  # type: ignore[arg-type]
            if target_idx < 0 or target_idx >= len(pages):
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="INVALID_TAB_INDEX",
                        message=f"Tab index {target_idx} out of range",
                    ),
                )
            await pages[target_idx].close()
            rem = self._context.pages
            self._page = rem[-1] if rem else await self._context.new_page()
            self._refs_valid = False
            output = f"Closed tab [{target_idx}]"
        else:
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=int((time.monotonic() - start_mono) * 1000),
                error=ErrorInfo(
                    code="UNSUPPORTED_OP",
                    message=f"Unsupported tab operation: {action.op}",
                ),
            )

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=output,
        )

    async def _execute_download(
        self,
        action: BrowserDownloadAction,
        ctx: ExecutionContext,
        started_at: datetime,
        start_mono: float,
    ) -> ActionResult:
        page = await self.ensure_browser(ctx.settings)

        # Destination directory
        if action.dest_dir:
            dl_dir = Path(action.dest_dir)
        elif ctx.settings and ctx.settings.browser.download_dir:
            dl_dir = Path(ctx.settings.browser.download_dir)
        else:
            dl_dir = Path.home() / "Downloads" / "local-control"

        dl_dir = dl_dir.expanduser().resolve()
        dl_dir.mkdir(parents=True, exist_ok=True)

        if action.ref:
            if (
                not self._refs_valid
                or self._snapshot_nav_id != self._nav_id
                or action.ref not in self._refs
            ):
                return ActionResult(
                    action_type=action.type,
                    success=False,
                    started_at=started_at,
                    duration_ms=int((time.monotonic() - start_mono) * 1000),
                    error=ErrorInfo(
                        code="browser_stale_ref",
                        message=f"Reference '{action.ref}' is stale. Capture a new snapshot.",
                    ),
                )
            click_loc = page.locator(f"[data-lc-ref='{action.ref}']")
        elif action.selector:
            click_loc = page.locator(action.selector)
        else:
            return ActionResult(
                action_type=action.type,
                success=False,
                started_at=started_at,
                duration_ms=int((time.monotonic() - start_mono) * 1000),
                error=ErrorInfo(
                    code="INVALID_PARAMS",
                    message="Neither ref nor selector provided for browser_download",
                ),
            )

        async with page.expect_download(timeout=30000) as download_info:
            await click_loc.first.click()

        download = await download_info.value
        suggested = download.suggested_filename
        target_file = dl_dir / suggested
        await download.save_as(str(target_file))

        if action.settle_ms > 0:
            await asyncio.sleep(action.settle_ms / 1000.0)

        duration_ms = int((time.monotonic() - start_mono) * 1000)
        return ActionResult(
            action_type=action.type,
            success=True,
            started_at=started_at,
            duration_ms=duration_ms,
            output=f"Downloaded '{suggested}' to {target_file}",
        )

    async def _take_snapshot(self, page: Page, max_nodes: int = 400) -> str:
        """Inspect page DOM, tag elements with refs, and return formatted compact tree."""
        js_code = f"""
        () => {{
            const maxNodes = {max_nodes};
            const elements = [];
            // Clean old refs
            document.querySelectorAll('[data-lc-ref]').forEach(el => el.removeAttribute('data-lc-ref'));

            const selector = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="tab"], h1, h2, h3, h4, h5, h6';
            const nodes = Array.from(document.querySelectorAll(selector));
            let refCounter = 1;

            for (const el of nodes) {{
                if (elements.length >= maxNodes) break;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                if (rect.width === 0 && rect.height === 0) continue;

                const ref = 'e' + refCounter++;
                el.setAttribute('data-lc-ref', ref);

                const tag = el.tagName.toLowerCase();
                let role = el.getAttribute('role') || tag;
                let text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                if (text.length > 80) text = text.slice(0, 80) + '...';

                let info = {{
                    ref: ref,
                    tag: tag,
                    role: role,
                    text: text
                }};

                if (tag === 'input') {{
                    info.type = el.type || 'text';
                    info.value = el.value || '';
                    info.placeholder = el.placeholder || '';
                    info.name = el.name || '';
                    info.role = (info.type === 'submit' || info.type === 'button') ? 'button' : 'textbox';
                }} else if (tag === 'textarea') {{
                    info.role = 'textarea';
                    info.value = el.value || '';
                    info.placeholder = el.placeholder || '';
                }} else if (tag === 'select') {{
                    info.role = 'combobox';
                    info.value = el.value || '';
                    info.options = Array.from(el.options).map(o => o.text);
                }} else if (tag === 'a') {{
                    info.role = 'link';
                    info.href = el.getAttribute('href') || '';
                }} else if (tag.startsWith('h') && tag.length === 2) {{
                    info.role = 'heading';
                    info.level = parseInt(tag[1]);
                }}

                elements.push(info);
            }}
            return elements;
        }}
        """
        raw_items: list[dict[str, Any]] = await page.evaluate(js_code)
        lines: list[str] = []
        self._refs.clear()

        for item in raw_items:
            ref = item["ref"]
            self._refs[ref] = item
            role = item.get("role", "element")
            text = item.get("text", "")
            itype = item.get("type")
            val = item.get("value")
            placeholder = item.get("placeholder")
            href = item.get("href")
            options = item.get("options")
            level = item.get("level")

            extras: list[str] = []
            if itype:
                extras.append(f"type={itype}")
            if val:
                extras.append(f'value="{val}"')
            if placeholder:
                extras.append(f'placeholder="{placeholder}"')
            if href:
                extras.append(f'href="{href}"')
            if options:
                extras.append(f"options={options}")
            if level:
                extras.append(f"level={level}")

            extra_str = f" ({', '.join(extras)})" if extras else ""
            lines.append(f'[{ref}] {role} "{text}"{extra_str}')

        snapshot_str = "\n".join(lines)
        self._last_snapshot = snapshot_str
        self._refs_valid = True
        self._snapshot_nav_id = self._nav_id
        return snapshot_str

    async def get_observation(self) -> BrowserObservation | None:
        """Extract browser observation details for planner and state."""
        if not self._context or not self._page or self._page.is_closed():
            return None
        try:
            url = self._page.url
            title = await self._page.title()
            tabs: list[BrowserTabInfo] = []
            active_idx = 0
            for idx, p in enumerate(self._context.pages):
                is_active = p == self._page
                if is_active:
                    active_idx = idx
                tabs.append(
                    BrowserTabInfo(
                        index=idx,
                        url=p.url,
                        title=await p.title() if not p.is_closed() else "",
                        active=is_active,
                    )
                )
            return BrowserObservation(
                url=url,
                title=title,
                snapshot=self._last_snapshot,
                tabs=tabs,
                active_tab_index=active_idx,
                tab_count=len(tabs),
                is_agent_browser_foreground=True,
            )
        except Exception as e:
            logger.warning("browser_tool.get_observation_failed", error=str(e))
            return None
