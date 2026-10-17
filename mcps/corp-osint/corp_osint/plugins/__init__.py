"""Plugin package — stage plugins, Entra/parking helpers, and the pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..plugin import Plugin
from .asn import AsnLookup
from .azure import AzureTenantLookup
from .dangling import DanglingCheck
from .dnslytics import DnslyticsGtmLookup
from .edgar import EdgarLookup
from .rdap import RdapLookup
from .resolve import DomainResolver
from .tenant_domains import TenantDomainLookup
from .urlscan import UrlscanLookup
from .web_search import PlaywrightWebSearch

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..http import AsyncHttpClient
    from .pipeline import CorpOsintPipeline

# Registry: plugin name → class (used by the orchestrator + CLI)
# CorpOsintPipeline is imported lazily via create_plugin / public API to avoid cycles.
PLUGIN_REGISTRY: dict[str, type[Plugin]] = {
    EdgarLookup.name: EdgarLookup,
    DomainResolver.name: DomainResolver,
    UrlscanLookup.name: UrlscanLookup,
    PlaywrightWebSearch.name: PlaywrightWebSearch,
    DnslyticsGtmLookup.name: DnslyticsGtmLookup,
    RdapLookup.name: RdapLookup,
    AzureTenantLookup.name: AzureTenantLookup,
    AsnLookup.name: AsnLookup,
    TenantDomainLookup.name: TenantDomainLookup,
    DanglingCheck.name: DanglingCheck,
}


def create_plugin(
    name: str,
    http: AsyncHttpClient,
    config: PipelineConfig | None = None,
    **kwargs,
) -> Plugin:
    """Instantiate a registered plugin by name."""
    cls = PLUGIN_REGISTRY.get(name)
    if cls is None:
        known = ", ".join(sorted(PLUGIN_REGISTRY))
        raise KeyError(f"Unknown plugin {name!r}. Known: {known}")
    # Pass common optional kwargs some plugins accept
    if config is not None:
        if name == UrlscanLookup.name and "api_key" not in kwargs:
            kwargs["api_key"] = config.urlscan_api_key
        if name == DnslyticsGtmLookup.name and "api_key" not in kwargs:
            kwargs["api_key"] = config.dnslytics_api_key
        if name == DanglingCheck.name:
            kwargs.setdefault("concurrency", config.limits.dangling_concurrency)
            kwargs.setdefault("check_rdap", config.limits.dangling_check_rdap)
    return cls(http, config=config, **kwargs)  # type: ignore[call-arg]


def list_plugins() -> list[dict[str, str]]:
    return [
        {"name": cls.name, "description": getattr(cls, "description", "") or cls.__doc__ or ""}
        for cls in PLUGIN_REGISTRY.values()
    ]


def __getattr__(name: str):
    if name == "CorpOsintPipeline":
        from .pipeline import CorpOsintPipeline

        return CorpOsintPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PLUGIN_REGISTRY",
    "create_plugin",
    "list_plugins",
    "CorpOsintPipeline",
    "AsnLookup",
    "AzureTenantLookup",
    "DnslyticsGtmLookup",
    "EdgarLookup",
    "RdapLookup",
    "DomainResolver",
    "TenantDomainLookup",
    "UrlscanLookup",
    "PlaywrightWebSearch",
    "DanglingCheck",
]
