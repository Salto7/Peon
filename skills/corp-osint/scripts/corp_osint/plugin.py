"""Single abstract Plugin base — every pipeline stage inherits this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

from .log import get_logger

if TYPE_CHECKING:
    from .config import PipelineConfig
    from .http import AsyncHttpClient


class Plugin(ABC):
    """
    Abstract base for all corp-OSINT modules.

    Implement ``run(**kwargs)`` to do the work. Optionally override
    ``enabled(config)`` and ``close()``.
    """

    name: str = "plugin"
    description: str = ""

    def __init__(
        self,
        http: Optional["AsyncHttpClient"] = None,
        config: Optional["PipelineConfig"] = None,
        **kwargs: Any,
    ) -> None:
        self.http = http
        self.config = config
        self.kwargs = kwargs
        self.log = get_logger(self.name)

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute this module and return structured results."""

    def enabled(self, config: Optional["PipelineConfig"] = None) -> bool:
        """Return False to skip this module for the given config."""
        return True

    async def close(self) -> None:
        """Optional cleanup (browsers, pools)."""
        return None


class BrowserSupport:
    """Optional mixin for plugins that need a shared Playwright browser (+ proxy)."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None

    def _proxy_kwargs(self) -> dict[str, Any]:
        cfg = getattr(self, "config", None)
        http = getattr(cfg, "http", None) if cfg is not None else None
        proxy = http.playwright_proxy() if http is not None else None
        return {"proxy": proxy} if proxy else {}

    async def ensure_browser(self, headless: bool = True):
        if self._browser:
            return self._context
        from playwright.async_api import async_playwright

        proxy_kw = self._proxy_kwargs()
        self._pw = await async_playwright().start()
        # Prefer launch-level proxy so all contexts inherit it
        self._browser = await self._pw.chromium.launch(headless=headless, **proxy_kw)
        ctx_kw: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
        }
        self._context = await self._browser.new_context(**ctx_kw)
        if proxy_kw:
            get_logger("browser").info("Playwright proxy %s", proxy_kw["proxy"].get("server"))
        return self._context

    async def close_browser(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
