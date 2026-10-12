"""ASN / IP discovery with ownership verification (async, light by default)."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
from typing import Any, Optional
from urllib.parse import quote

from ..plugin import Plugin
from ..http import AsyncHttpClient

RIPE_NET = "https://stat.ripe.net/data/network-info/data.json?resource={ip}"
RIPE_AS = "https://stat.ripe.net/data/as-overview/data.json?resource=AS{asn}"
RIPE_PREF = "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn}"
RDAP_IP = "https://rdap.org/ip/{ip}"
_STOP = {"inc", "corp", "corporation", "llc", "ltd", "limited", "company", "the", "co", "plc", "group"}


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _STOP and len(t) > 2]


def _match(company: str, *blobs: Optional[str]) -> dict[str, Any]:
    tokens = _tokens(company)
    hay = " ".join(b for b in blobs if b).lower()
    if not tokens or not hay:
        return {"matched": False, "confidence": "low", "hits": [], "evidence": "No comparable org text"}
    hits = [t for t in tokens if t in hay]
    ratio = len(hits) / len(tokens)
    if ratio >= 0.5 and hits:
        return {
            "matched": True,
            "confidence": "high" if ratio >= 0.8 else "medium",
            "hits": hits,
            "evidence": f"tokens {hits} cover {ratio:.0%}",
        }
    return {"matched": False, "confidence": "low", "hits": hits, "evidence": f"weak match hits={hits}"}


class AsnLookup(Plugin):
    name = "asn"


    async def run(self, **kwargs):
        domain = kwargs.pop("domain", None)
        if not domain:
            raise ValueError("domain required")
        return await self.enrich(str(domain), **kwargs)

    async def enrich(self, domain: str, **kwargs: Any) -> dict[str, Any]:
        company = kwargs.get("company") or domain
        deep = bool(kwargs.get("deep"))
        ips = await asyncio.to_thread(self._resolve_ips, domain)
        asns: dict[int, dict[str, Any]] = {}
        errors: list[str] = []

        for ip in ips:
            mapped = await self._map_ip(ip)
            errors.extend(mapped.get("errors") or [])
            for a in mapped.get("asns") or []:
                asn = int(a["asn"])
                entry = asns.setdefault(
                    asn,
                    {
                        "asn": asn,
                        "name": a.get("name"),
                        "description": a.get("description"),
                        "country_code": a.get("country_code"),
                        "observed_ips": [],
                        "prefixes": list(mapped.get("prefixes") or []),
                    },
                )
                if ip not in entry["observed_ips"]:
                    entry["observed_ips"].append(ip)
                for k in ("name", "description", "country_code"):
                    if a.get(k) and not entry.get(k):
                        entry[k] = a[k]

        out_asns: list[dict[str, Any]] = []
        for asn, base in list(asns.items())[:3]:
            meta = await self._asn_meta(asn)
            prefixes = base.get("prefixes") or []
            if deep:
                prefixes = (await self._asn_prefixes(asn)) or prefixes
            sample_ips = list(base["observed_ips"])
            verified = []
            for ip in sample_ips[:6]:
                rdap = await self._ip_rdap(ip)
                whois = await self._ip_whois(ip)
                own = _match(
                    company,
                    (rdap.get("rdap") or {}).get("org"),
                    (rdap.get("rdap") or {}).get("name"),
                    whois.get("org"),
                    whois.get("netname"),
                    meta.get("name"),
                    base.get("name"),
                    base.get("description"),
                )
                verified.append(
                    {
                        "ip": ip,
                        "rdap": rdap.get("rdap"),
                        "whois": {k: whois.get(k) for k in ("used", "org", "netname", "error")},
                        "ownership": own,
                    }
                )
            hits = [v for v in verified if v["ownership"]["matched"]]
            out_asns.append(
                {
                    "asn": asn,
                    "name": meta.get("name") or base.get("name"),
                    "description": meta.get("description_short") or base.get("description"),
                    "country_code": base.get("country_code"),
                    "prefixes": [{"prefix": p} if isinstance(p, str) else p for p in prefixes[:15]],
                    "observed_ips": base["observed_ips"],
                    "sampled_ips": verified,
                    "ownership_verified": bool(hits),
                    "ownership_confidence": hits[0]["ownership"]["confidence"] if hits else "low",
                    "sources": [RIPE_AS.format(asn=asn)],
                }
            )

        if not ips:
            errors.append(f"No DNS A/AAAA for {domain}")
        elif not out_asns:
            errors.append("No ASN mapping")

        return {"domain": domain, "resolved_ips": ips, "asns": out_asns, "errors": errors}

    @staticmethod
    def _resolve_ips(domain: str) -> list[str]:
        from ..util import normalize_domain

        domain = normalize_domain(domain) or domain
        ips: list[str] = []
        try:
            for fam, _, _, _, sockaddr in socket.getaddrinfo(domain, None):
                ip = sockaddr[0]
                if fam in (socket.AF_INET, socket.AF_INET6) and ip not in ips:
                    ips.append(ip)
        except socket.gaierror:
            return []
        return ips

    async def _map_ip(self, ip: str) -> dict[str, Any]:
        errors: list[str] = []
        # Cymru whois (thread)
        cymru = await asyncio.to_thread(self._cymru, ip)
        if cymru.get("asns"):
            return cymru
        if cymru.get("error"):
            errors.append(cymru["error"])
        # RIPE
        try:
            doc = await self.http.get_json(RIPE_NET.format(ip=quote(ip)))
            data = doc.get("data") or {}
            prefs = data.get("prefix") or []
            if isinstance(prefs, str):
                prefs = [prefs]
            asns = []
            for a in data.get("asns") or []:
                asns.append({"asn": int(str(a).lstrip("AS")), "source": "ripe"})
            if asns:
                return {"ip": ip, "asns": asns, "prefixes": prefs, "errors": errors}
        except Exception as e:  # noqa: BLE001
            errors.append(f"ripe: {e}")
        return {"ip": ip, "asns": [], "prefixes": [], "errors": errors}

    @staticmethod
    def _cymru(ip: str) -> dict[str, Any]:
        if not shutil.which("whois"):
            return {"ip": ip, "asns": [], "prefixes": [], "error": "whois missing"}
        import subprocess

        try:
            proc = subprocess.run(
                ["whois", "-h", "whois.cymru.com", f" -v {ip}"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except Exception as e:  # noqa: BLE001
            return {"ip": ip, "asns": [], "prefixes": [], "error": str(e)}
        asns, prefixes = [], []
        for line in (proc.stdout or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            asn = int(parts[0])
            prefix = parts[2]
            as_name = parts[6] if len(parts) > 6 else None
            cc = parts[3] if len(parts) > 3 else None
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
            asns.append(
                {
                    "asn": asn,
                    "name": as_name,
                    "description": as_name,
                    "country_code": cc,
                    "source": "cymru",
                }
            )
        return {"ip": ip, "asns": asns, "prefixes": prefixes, "error": None if asns else "no cymru rows"}

    async def _asn_meta(self, asn: int) -> dict[str, Any]:
        try:
            doc = await self.http.get_json(RIPE_AS.format(asn=asn))
            data = doc.get("data") or {}
            return {"asn": asn, "name": data.get("holder"), "description_short": data.get("holder")}
        except Exception:  # noqa: BLE001
            return {"asn": asn}

    async def _asn_prefixes(self, asn: int) -> list[str]:
        try:
            doc = await self.http.get_json(RIPE_PREF.format(asn=asn))
            return [p.get("prefix") for p in (doc.get("data") or {}).get("prefixes") or [] if p.get("prefix")][:20]
        except Exception:  # noqa: BLE001
            return []

    async def _ip_rdap(self, ip: str) -> dict[str, Any]:
        try:
            status, body, _ = await self.http.get(
                RDAP_IP.format(ip=quote(ip)),
                headers={"Accept": "application/rdap+json, application/json"},
            )
            if status >= 400:
                return {"ip": ip, "rdap": None, "error": f"HTTP {status}"}
            doc = json.loads(body.decode("utf-8", errors="replace"))
            org = None
            for ent in doc.get("entities") or []:
                vcard = ent.get("vcardArray")
                if isinstance(vcard, list) and len(vcard) >= 2:
                    for item in vcard[1]:
                        if isinstance(item, list) and item and item[0] in {"org", "fn"} and len(item) >= 4:
                            if isinstance(item[3], str) and item[3].strip():
                                org = item[3].strip()
                                break
                if org:
                    break
            return {
                "ip": ip,
                "rdap": {"name": doc.get("name"), "org": org, "handle": doc.get("handle")},
                "error": None,
            }
        except Exception as e:  # noqa: BLE001
            return {"ip": ip, "rdap": None, "error": str(e)}

    async def _ip_whois(self, ip: str) -> dict[str, Any]:
        if not shutil.which("whois"):
            return {"used": False, "org": None, "netname": None, "error": "whois missing"}
        try:
            proc = await asyncio.create_subprocess_exec(
                "whois", ip, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            text = (out or b"").decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return {"used": True, "org": None, "netname": None, "error": str(e)}
        org = netname = None
        m = re.search(r"(?im)^(OrgName|Organization|organisation|org-name):\s*(.+)$", text)
        if m:
            org = m.group(2).strip()
        m = re.search(r"(?im)^(netname|NetName):\s*(.+)$", text)
        if m:
            netname = m.group(2).strip()
        return {"used": True, "org": org, "netname": netname, "error": None}
