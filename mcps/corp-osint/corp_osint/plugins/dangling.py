"""Dangling / parked / unregistered domain checks for Entra-verified domains.

A domain is dangling when it is verified in the company Entra tenant AND any of:
- parking / for-sale page
- DNS NXDOMAIN
- RDAP unregistered / not found
- HTTP unreachable (abandoned web presence still in IdP)
"""

from __future__ import annotations

import asyncio
import re
import socket
from typing import Any

from ..http import AsyncHttpClient
from ..plugin import Plugin
from ..util import is_apex_domain, normalize_domain
from .parking import detect_parking

_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")

RDAP_URL = "https://rdap.org/domain/{domain}"

# Statuses that count as dangling when Entra-verified
DANGLING_KINDS = frozenset({"parked", "nxdomain", "unregistered", "unreachable"})


class DanglingCheck(Plugin):
    name = "dangling_check"
    description = (
        "Probe Entra-verified apex domains for parking, NXDOMAIN, unregistered, "
        "or unreachable web — flag dangling Entra risks (subdomains skipped)"
    )

    def __init__(
        self,
        http: AsyncHttpClient,
        config=None,
        concurrency: int = 12,
        http_timeout: float = 8.0,
        check_rdap: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(http, config=config, **kwargs)
        self.concurrency = concurrency
        self.http_timeout = http_timeout
        self.check_rdap = check_rdap

    @staticmethod
    def _dns_lookup(domain: str) -> dict[str, Any]:
        """Blocking DNS probe run in a thread."""
        out: dict[str, Any] = {
            "domain": domain,
            "resolves": False,
            "nxdomain": False,
            "ips": [],
            "error": None,
        }
        try:
            infos = socket.getaddrinfo(domain, None)
            ips = sorted({i[4][0] for i in infos if i and i[4]})
            out["resolves"] = bool(ips)
            out["ips"] = ips[:8]
        except socket.gaierror as e:
            # EAI_NONAME / EAI_NODATA ≈ NXDOMAIN or no answer
            err = str(e).lower()
            out["error"] = str(e)
            if "name or service not known" in err or "nodename nor servname" in err or getattr(e, "errno", None) in {
                socket.EAI_NONAME,
                getattr(socket, "EAI_NODATA", -5),
            }:
                out["nxdomain"] = True
            else:
                out["nxdomain"] = True  # treat hard resolve failures as dangling-relevant
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
        return out

    async def _probe_http(self, domain: str) -> dict[str, Any]:
        last_err: str | None = None
        for url in (f"https://{domain}/", f"http://{domain}/"):
            status, body, err = await self.http.probe(url, timeout=self.http_timeout)
            if err:
                last_err = err
                continue
            if status is None:
                last_err = "no response"
                continue
            # Any HTTP response means the host answers (404/403 ≠ dangling unreachable)
            title = ""
            if body:
                m = _TITLE_RE.search(body)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()
            text = body[:4000] if body else ""
            parked, marker = detect_parking(title=title, text=text, html=body or "", final_url=url)
            return {
                "ok": True,
                "status": status,
                "url": url,
                "title": title,
                "is_parked": parked,
                "parking_marker": marker,
                "error": None,
            }
        return {
            "ok": False,
            "status": None,
            "url": None,
            "title": None,
            "is_parked": False,
            "parking_marker": None,
            "error": last_err or "unreachable",
        }

    async def _rdap_available(self, domain: str) -> dict[str, Any]:
        """Best-effort: 404 ⇒ likely unregistered / not in RDAP."""
        url = RDAP_URL.format(domain=domain)
        try:
            status, _, _ = await self.http.request(
                "GET",
                url,
                headers={"Accept": "application/rdap+json, application/json"},
                timeout=10.0,
                attempts=1,
                retry_on_client_error=False,
            )
            if status == 404:
                return {"checked": True, "unregistered": True, "status": 404}
            if status and status < 400:
                return {"checked": True, "unregistered": False, "status": status}
            return {"checked": True, "unregistered": False, "status": status}
        except Exception as e:  # noqa: BLE001
            return {"checked": False, "unregistered": False, "error": str(e)}

    async def check_one(
        self,
        domain: str,
        *,
        entra_verified: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        domain = normalize_domain(domain) or domain
        # Always DNS + HTTP: local resolver failures must not alone classify NXDOMAIN
        dns = await asyncio.to_thread(self._dns_lookup, domain)
        http_info = await self._probe_http(domain)
        rdap: dict[str, Any] = {"checked": False, "unregistered": False}

        if http_info.get("is_parked"):
            kind = "parked"
        elif http_info.get("ok"):
            kind = "live"
        elif not dns.get("resolves"):
            # Dead DNS + dead HTTP → nxdomain / unregistered (confirm with RDAP when possible)
            kind = "nxdomain"
            if self.check_rdap:
                rdap = await self._rdap_available(domain)
                if rdap.get("unregistered"):
                    kind = "unregistered"
                elif rdap.get("checked") and not rdap.get("unregistered"):
                    # Registered in RDAP but no DNS/HTTP → still dangling (stale DNS)
                    kind = "nxdomain"
        else:
            # DNS works, site does not → abandoned / filtered host
            kind = "unreachable"

        dangling = bool(entra_verified and kind in DANGLING_KINDS)
        detail = None
        if dangling:
            detail = (
                f"Entra-verified domain is {kind}"
                + (f" (marker={http_info.get('parking_marker')})" if kind == "parked" else "")
                + (f"; tenant={tenant_id}" if tenant_id else "")
                + ". Dangling risk: abandoned/parked/unregistered presence still in Entra ID."
            )

        return {
            "domain": domain,
            "kind": kind,
            "is_parked": kind == "parked" or bool(http_info.get("is_parked")),
            "parking_marker": http_info.get("parking_marker"),
            "dns": dns,
            "http": {
                "ok": http_info.get("ok"),
                "status": http_info.get("status"),
                "url": http_info.get("url"),
                "title": http_info.get("title"),
                "error": http_info.get("error"),
            },
            "rdap": rdap,
            "entra_verified": entra_verified,
            "tenant_id": tenant_id,
            "dangling_entra": dangling,
            "dangling_entra_detail": detail,
        }

    async def run(
        self,
        domains: list[str] | None = None,
        *,
        entra_verified_domains: list[str] | None = None,
        tenant_id: str | None = None,
        max_domains: int = 200,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Check domains for parked / nxdomain / unregistered / unreachable.

        ``entra_verified_domains`` are treated as Entra-verified (dangling if dead).
        Extra ``domains`` are probed but only flagged dangling when also Entra-verified.

        **Apex only** — subdomains (``app.example.com``) are skipped; RDAP/DNS dangling
        risk applies to registrable domains, not hostnames under an owned zone.
        """
        skipped_subdomains = 0

        def _apex_only(values: list[str] | None) -> set[str]:
            nonlocal skipped_subdomains
            out: set[str] = set()
            for x in values or []:
                d = normalize_domain(x)
                if not d or d.endswith(".onmicrosoft.com"):
                    continue
                if not is_apex_domain(d):
                    skipped_subdomains += 1
                    continue
                out.add(d)
            return out

        verified = _apex_only(list(entra_verified_domains or []))
        extra = _apex_only(list(domains or []))
        # Prefer scanning Entra-verified set; include extras if room
        ordered = sorted(verified) + sorted(d for d in extra if d not in verified)
        targets = ordered[: max(0, max_domains)]
        self.log.info(
            "probing %d apex domain(s) (entra_verified=%d skipped_subdomains=%d concurrency=%d)",
            len(targets),
            len(verified),
            skipped_subdomains,
            self.concurrency,
        )

        sem = asyncio.Semaphore(self.concurrency)
        results: list[dict[str, Any]] = []
        done = {"n": 0}

        async def _one(dom: str) -> None:
            async with sem:
                try:
                    row = await self.check_one(
                        dom,
                        entra_verified=dom in verified,
                        tenant_id=tenant_id if dom in verified else None,
                    )
                except Exception as e:  # noqa: BLE001
                    self.log.debug("check failed %s: %s", dom, e)
                    row = {
                        "domain": dom,
                        "kind": "error",
                        "is_parked": False,
                        "entra_verified": dom in verified,
                        "tenant_id": tenant_id if dom in verified else None,
                        "dangling_entra": False,
                        "error": str(e),
                    }
                results.append(row)
                done["n"] += 1
                if row.get("dangling_entra"):
                    self.log.info(
                        "dangling %s kind=%s",
                        dom,
                        row.get("kind"),
                    )
                else:
                    self.log.debug("%s → %s", dom, row.get("kind"))
                if done["n"] % 25 == 0 or done["n"] == len(targets):
                    self.log.info("progress %d/%d", done["n"], len(targets))

        await asyncio.gather(*[_one(d) for d in targets])
        results.sort(key=lambda r: (0 if r.get("dangling_entra") else 1, r.get("domain") or ""))

        dangling = [r for r in results if r.get("dangling_entra")]
        parked = [r for r in results if r.get("is_parked")]
        by_kind: dict[str, int] = {}
        for r in results:
            k = r.get("kind") or "unknown"
            by_kind[k] = by_kind.get(k, 0) + 1

        self.log.info(
            "finished scanned=%d dangling=%d parked=%d by_kind=%s",
            len(results),
            len(dangling),
            len(parked),
            by_kind,
        )
        return {
            "tenant_id": tenant_id,
            "scanned": len(results),
            "counts": {
                "scanned": len(results),
                "dangling": len(dangling),
                "parked": len(parked),
                **{f"kind_{k}": v for k, v in sorted(by_kind.items())},
            },
            "dangling_domains": dangling,
            "parked_domains": parked,
            "results": results,
            "errors": [r["error"] for r in results if r.get("error")],
        }
