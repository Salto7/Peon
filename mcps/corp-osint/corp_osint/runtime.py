"""Shared pipeline / plugin execution used by the CLI and MCP wrappers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import (
    HttpConfig,
    LimitsConfig,
    PipelineConfig,
    StageConfig,
    ThrottleConfig,
    apply_stage_selection,
    resolve_proxy_url,
)
from .http import AsyncHttpClient
from .log import get_logger
from .plugins import PLUGIN_REGISTRY, create_plugin
from .plugins.entra import clear_oidc_cache
from .plugins.pipeline import CorpOsintPipeline
from .report import ensure_summary
from .util import normalize_domain

log = get_logger("runtime")


def _limit(value, default=None):
    """Optional work limit: missing / null / <=0 means unlimited."""
    raw = default if value is None or value == "" else value
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def http_config_from_values(values: Mapping[str, Any]) -> HttpConfig:
    """Build HttpConfig from a flat values mapping (CLI/MCP)."""
    http = HttpConfig()
    if values.get("direct"):
        http.proxy = None
    elif "proxy" in values and values.get("proxy") is not None:
        http.proxy = resolve_proxy_url(str(values.get("proxy")))
    if values.get("no_proxy") is not None:
        http.no_proxy = tuple(
            item.strip()
            for item in str(values["no_proxy"]).replace(";", ",").split(",")
            if item.strip()
        )
    return http


def pipeline_config_from_values(
    values: Mapping[str, Any],
    *,
    require_company: bool = True,
) -> PipelineConfig | None:
    """Build PipelineConfig; return None when company is optional and missing."""
    company = str(values.get("company") or "").strip()
    if not company:
        if require_company:
            raise ValueError("company is required")
        return None
    stages = apply_stage_selection(
        StageConfig(),
        skip=values.get("skip"),
        only=values.get("only"),
    )
    return PipelineConfig(
        company=company,
        cik=values.get("cik") or None,
        ticker=values.get("ticker") or None,
        domain=normalize_domain(values.get("domain")),
        stages=stages,
        http=http_config_from_values(values),
    )


def plugin_kwargs(
    name: str,
    values: Mapping[str, Any],
    *,
    limits: LimitsConfig | None = None,
    playwright_engine: str = "bing",
    asn_deep: bool = False,
) -> dict[str, Any]:
    """Map flat values → kwargs for ``plugin.run``."""
    lim = limits or LimitsConfig()
    company = values.get("company")
    domain = normalize_domain(values.get("domain"))
    tenant_id = values.get("tenant_id")

    if name == "edgar":
        return {k: values[k] for k in ("company", "ticker", "cik") if values.get(k)}

    if name == "resolve_domains":
        resolved_name = values.get("name") or company or domain
        if not resolved_name:
            raise ValueError("resolve_domains needs company and/or domain")
        parent_domain = values.get("parent_domain") or domain
        # When a domain is known, probe it only — do not invent .com/.net slugs
        allow_guess = values.get("allow_guess")
        if allow_guess is None:
            allow_guess = not bool(parent_domain)
        return {
            "name": resolved_name,
            "parent_domain": parent_domain,
            "allow_guess": bool(allow_guess),
        }

    if name == "playwright_search":
        if not company:
            raise ValueError("playwright_search needs company")
        return {
            "company": company,
            "domain": domain,
            "engine": values.get("engine")
            or values.get("playwright_engine")
            or playwright_engine,
            "max_queries": int(
                values.get(
                    "max_queries",
                    values.get("playwright_max_queries", lim.playwright_max_queries),
                )
            ),
        }

    if name == "urlscan":
        return {
            "domain": domain,
            "company": company,
            "size": int(values.get("size", lim.urlscan_size)),
        }

    if name == "dnslytics_gtm":
        return {
            "domains": list(values.get("domains") or []),
            "domain": domain,
            "max_domains": int(values.get("max_domains", lim.dnslytics_max_domains)),
            "max_gtm_reverse": int(
                values.get("max_gtm_reverse", lim.dnslytics_max_gtm_reverse)
            ),
        }

    if name == "rdap":
        if not domain:
            raise ValueError("rdap needs domain")
        return {"domain": domain, "force_whois": bool(values.get("force_whois", False))}

    if name == "azure_tenant":
        kwargs: dict[str, Any] = {
            "domain": domain,
            "domains": values.get("domains"),
            "expected_tenant_id": values.get("expected_tenant_id") or tenant_id,
            "max_domains": _limit(values.get("max_domains"), lim.entra_verify_max),
            "concurrency": int(
                values.get("concurrency", lim.entra_verify_concurrency)
            ),
        }
        # CLI convenience: tenant_id + single domain → verify mode
        if tenant_id and domain and not kwargs.get("domains"):
            kwargs["domains"] = [domain]
        return kwargs

    if name == "asn":
        if not domain:
            raise ValueError("asn needs domain")
        return {
            "domain": domain,
            "company": company,
            "deep": bool(values.get("deep", asn_deep)),
        }

    if name == "tenant_domains":
        return {
            "domain": domain,
            "tenant_id": tenant_id,
            "max_domains": _limit(values.get("max_domains"), lim.tenant_domains_max),
        }

    if name == "dangling_check":
        domains = list(values.get("domains") or ([] if not domain else [domain]))
        verified = list(
            values.get("entra_verified_domains")
            or (domains if tenant_id else [])
        )
        return {
            "domains": domains,
            "entra_verified_domains": verified,
            "tenant_id": tenant_id,
            "max_domains": _limit(values.get("max_domains"), lim.dangling_max_domains),
        }

    raise ValueError(f"Unknown plugin: {name}")


def wrap_plugin_result(name: str, values: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    """Normalize single-plugin output so ``summary`` is always present."""
    if isinstance(raw, dict) and "summary" in raw and "query" in raw:
        return ensure_summary(raw)
    company = values.get("company")
    domain = values.get("domain")
    wrapped: dict[str, Any] = {
        "query": company or domain or name,
        "plugin": name,
        "parent": {"name": company, "primary_domain": domain},
        "result": raw,
        "domains": [],
        "entities": [],
        "dangling_domains": [],
    }
    if isinstance(raw, dict):
        if raw.get("domain") and raw.get("azure_tenant"):
            wrapped["domains"] = [
                {
                    "domain": raw["domain"],
                    "discovery": {
                        "relationship_verified": True,
                        "confidence": "high",
                        "evidence": f"plugin {name}",
                        "sources": [],
                    },
                    "azure_tenant": raw.get("azure_tenant"),
                }
            ]
            wrapped["parent"]["entra_tenant_id"] = (raw.get("azure_tenant") or {}).get(
                "tenant_id"
            )
        if name == "tenant_domains" and isinstance(raw.get("domains"), list):
            wrapped["tenant_domains"] = raw
            wrapped["parent"]["entra_tenant_id"] = raw.get("tenant_id")
        if isinstance(raw.get("dangling_domains"), list):
            wrapped["dangling_domains"] = raw["dangling_domains"]
    return ensure_summary(wrapped)


async def run_pipeline(values: Mapping[str, Any]) -> dict[str, Any]:
    """Execute the full CorpOsintPipeline for ``values``."""
    cfg = pipeline_config_from_values(values, require_company=True)
    if cfg is None:  # require_company=True raises first; guard for -O
        raise ValueError("company is required")
    log.info("running full pipeline company=%s", cfg.company)
    return await CorpOsintPipeline(cfg).run()


async def run_plugin(name: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one registered plugin (or the full pipeline by name)."""
    if name in (CorpOsintPipeline.name, "pipeline"):
        return await run_pipeline(values)
    if name not in PLUGIN_REGISTRY:
        known = ", ".join(sorted(PLUGIN_REGISTRY) + [CorpOsintPipeline.name])
        raise ValueError(f"Unknown plugin {name!r}. Known: {known}")

    if values.get("domain"):
        values = {**values, "domain": normalize_domain(values["domain"])}
    # The OIDC cache is meant to live for one invocation only (see entra.py);
    # the MCP server process is long-lived for a whole project/sandbox, so a
    # standalone plugin call (azure_tenant/tenant_domains/dangling_check) must
    # clear it itself the way run_pipeline already does, or a later call with
    # different arguments for the same domain silently replays a stale result.
    clear_oidc_cache()
    cfg = pipeline_config_from_values(values, require_company=False)
    http_cfg = cfg.http if cfg else http_config_from_values(values)
    throttle = cfg.throttle if cfg else ThrottleConfig()
    limits = cfg.limits if cfg else LimitsConfig()
    engine = cfg.playwright_engine if cfg else "bing"
    deep = bool(cfg.asn_deep) if cfg else False

    log.info(
        "running plugin=%s company=%s domain=%s",
        name,
        values.get("company"),
        values.get("domain"),
    )
    async with AsyncHttpClient(http_cfg, throttle) as http:
        plugin = create_plugin(name, http, cfg)
        try:
            kwargs = plugin_kwargs(
                name,
                values,
                limits=limits,
                playwright_engine=engine,
                asn_deep=deep,
            )
            log.debug("plugin kwargs keys=%s", sorted(kwargs))
            raw = await plugin.run(**kwargs)
            return wrap_plugin_result(name, values, raw)
        finally:
            await plugin.close()
