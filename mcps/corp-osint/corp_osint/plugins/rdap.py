"""Domain RDAP (+ optional WHOIS fallback) — async."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any, Optional

from ..plugin import Plugin
from ..http import AsyncHttpClient

RDAP_URL = "https://rdap.org/domain/{domain}"


def _vcard_get(entity: dict[str, Any], key: str) -> Optional[str]:
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2:
        return None
    for item in vcard[1]:
        if isinstance(item, list) and item and item[0] == key and len(item) >= 4:
            val = item[3]
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def parse_rdap(doc: dict[str, Any]) -> dict[str, Any]:
    registrar = registrant_org = registrant_country = None
    for ent in doc.get("entities") or []:
        roles = set(ent.get("roles") or [])
        if "registrar" in roles:
            registrar = _vcard_get(ent, "org") or _vcard_get(ent, "fn") or ent.get("handle")
        if "registrant" in roles:
            registrant_org = _vcard_get(ent, "org") or _vcard_get(ent, "fn")
            vcard = ent.get("vcardArray")
            if isinstance(vcard, list) and len(vcard) >= 2:
                for item in vcard[1]:
                    if isinstance(item, list) and item and item[0] == "adr":
                        params = item[1] if isinstance(item[1], dict) else {}
                        if params.get("cc"):
                            registrant_country = str(params["cc"])
        for sub in ent.get("entities") or []:
            if "registrant" in set(sub.get("roles") or []) and not registrant_org:
                registrant_org = _vcard_get(sub, "org") or _vcard_get(sub, "fn")
    events = {
        e.get("eventAction"): e.get("eventDate")
        for e in doc.get("events") or []
        if e.get("eventAction")
    }
    nameservers = []
    for ns in doc.get("nameservers") or []:
        ldh = ns.get("ldhName") or ns.get("unicodeName")
        if ldh:
            nameservers.append(str(ldh).lower().rstrip("."))
    related = None
    for link in doc.get("links") or []:
        if link.get("rel") == "related" and link.get("href"):
            related = link["href"]
            break
    return {
        "registrar": registrar,
        "registered": events.get("registration"),
        "expires": events.get("expiration"),
        "last_changed": events.get("last changed"),
        "nameservers": nameservers,
        "registrant_org": registrant_org,
        "registrant_country": registrant_country,
        "status": doc.get("status") or [],
        "rdap_url": related,
        "handle": doc.get("handle"),
        "_related": related,
    }


class RdapLookup(Plugin):
    name = "rdap"


    async def run(self, **kwargs):
        domain = kwargs.pop("domain", None)
        if not domain:
            raise ValueError("domain required")
        return await self.enrich(str(domain), **kwargs)

    async def enrich(self, domain: str, **kwargs: Any) -> dict[str, Any]:
        from ..util import normalize_domain

        domain = normalize_domain(domain) or domain
        force_whois = bool(kwargs.get("force_whois"))
        self.log.info("progress [rdap] lookup %s", domain)
        url = RDAP_URL.format(domain=domain)
        status, body, _ = await self.http.get(
            url, headers={"Accept": "application/rdap+json, application/json"}
        )
        rdap = None
        err = None
        if status == 404:
            err = "RDAP not found"
        elif status >= 400:
            err = f"RDAP HTTP {status}"
        else:
            doc = json.loads(body.decode("utf-8", errors="replace"))
            rdap = parse_rdap(doc)
            # follow registrar related RDAP if thin
            if not rdap.get("registrant_org") and rdap.get("_related"):
                try:
                    st2, body2, _ = await self.http.get(
                        rdap["_related"],
                        headers={"Accept": "application/rdap+json, application/json"},
                    )
                    if st2 < 400:
                        richer = parse_rdap(json.loads(body2.decode("utf-8", errors="replace")))
                        for k in (
                            "registrant_org",
                            "registrant_country",
                            "registrar",
                            "registered",
                            "expires",
                            "nameservers",
                            "status",
                        ):
                            if richer.get(k) and not rdap.get(k):
                                rdap[k] = richer[k]
                        if richer.get("registrant_org"):
                            rdap["rdap_url"] = rdap["_related"]
                except Exception:  # noqa: BLE001
                    pass
            rdap.pop("_related", None)
            rdap["rdap_url"] = rdap.get("rdap_url") or url

        whois: dict[str, Any] = {"used_fallback": False, "raw_excerpt": None}
        if force_whois or not rdap or not rdap.get("registrant_org"):
            whois = await self._whois(domain)
            if whois.get("registrant_org") and rdap and not rdap.get("registrant_org"):
                rdap["registrant_org"] = whois["registrant_org"]

        return {"domain": domain, "rdap": rdap, "whois": whois, "error": err}

    async def _whois(self, domain: str) -> dict[str, Any]:
        if not shutil.which("whois"):
            return {
                "used_fallback": False,
                "raw_excerpt": None,
                "error": "whois binary not installed",
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                "whois",
                domain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=25)
        except Exception as e:  # noqa: BLE001
            return {"used_fallback": True, "raw_excerpt": None, "error": str(e)}
        text = (out or b"").decode("utf-8", errors="replace") + (err or b"").decode(
            "utf-8", errors="replace"
        )
        org = None
        m = re.search(r"(?im)^(Registrant Organization|OrgName|organisation):\s*(.+)$", text)
        if m:
            org = m.group(2).strip()
        return {
            "used_fallback": True,
            # Omit raw_excerpt by default — huge and redundant with parsed fields
            "raw_excerpt": None,
            "registrant_org": org,
            "error": None,
        }
