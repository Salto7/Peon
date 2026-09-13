#!/usr/bin/env python3
"""entra-osint CLI — resolve Entra tenant IDs via Azure OpenID configuration.

Primary endpoint (per domain):
  https://login.microsoftonline.com/<domain>/v2.0/.well-known/openid-configuration

issuer field looks like:
  https://login.microsoftonline.com/<tenant-id>/v2.0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent
if str(_ASSETS) not in sys.path:
    sys.path.insert(0, str(_ASSETS))

from lib.http import try_fetch_json  # noqa: E402

_OPENID_TMPL = (
    "https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration"
)
_ISSUER_TENANT_RE = re.compile(
    r"https://login\.microsoftonline\.com/([0-9a-fA-F-]{36})/v2\.0",
    re.I,
)
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.I,
)
_RAW = Path("workspace/raw/entra-osint")


def _normalize_domain(token: str) -> str:
    t = (token or "").strip().lower()
    t = t.removeprefix("https://").removeprefix("http://")
    t = t.split("/")[0].split(":")[0].lstrip(".")
    if t.startswith("*."):
        t = t[2:]
    return t


def _valid_domain(domain: str) -> bool:
    return bool(domain and _DOMAIN_RE.match(domain))


def extract_tenant_id(issuer: str) -> str:
    m = _ISSUER_TENANT_RE.search(issuer or "")
    return m.group(1).lower() if m else ""


def lookup_tenant(domain: str) -> dict:
    """Query OpenID configuration for one domain; return structured result."""
    domain = _normalize_domain(domain)
    row: dict = {
        "domain": domain,
        "tenant_id": "",
        "issuer": "",
        "ok": False,
        "http_status": 0,
        "error": "",
        "openid_url": "",
    }
    if not _valid_domain(domain):
        row["error"] = "invalid domain"
        return row

    url = _OPENID_TMPL.format(domain=domain)
    row["openid_url"] = url
    status, data, err = try_fetch_json(url)
    row["http_status"] = status

    _RAW.mkdir(parents=True, exist_ok=True)
    raw_path = _RAW / f"openid-{domain.replace('.', '_')}.json"
    if data is not None:
        raw_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        row["raw"] = str(raw_path)
        issuer = str(data.get("issuer") or "")
        row["issuer"] = issuer
        tenant = extract_tenant_id(issuer)
        if tenant:
            row["tenant_id"] = tenant
            row["ok"] = True
        else:
            row["error"] = f"issuer missing tenant GUID: {issuer!r}"
    else:
        raw_path.write_text(
            json.dumps({"error": err, "http_status": status}, indent=2) + "\n",
            encoding="utf-8",
        )
        row["raw"] = str(raw_path)
        row["error"] = err or f"http {status}"
    return row


def cmd_tenant(args: argparse.Namespace) -> int:
    domain = " ".join(args.domain).strip() if isinstance(args.domain, list) else args.domain
    row = lookup_tenant(domain)
    print(json.dumps(row, indent=2))
    return 0 if row.get("ok") else 1


def _read_domains(paths: list[str]) -> list[str]:
    tokens: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            tokens.append(s)
    if not tokens and not sys.stdin.isatty():
        for ln in sys.stdin:
            s = ln.strip()
            if s and not s.startswith("#"):
                tokens.append(s)
    out: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        d = _normalize_domain(t)
        if d and d not in seen and _valid_domain(d):
            seen.add(d)
            out.append(d)
    return out


def _domains_from_inventory(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    domains: list[str] = []
    rows = data.get("domains") if isinstance(data, dict) else data
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                domains.append(row)
            elif isinstance(row, dict):
                domains.append(str(row.get("domain") or row.get("name") or ""))
    return [
        d
        for d in (_normalize_domain(x) for x in domains)
        if d and _valid_domain(d)
    ]


def _write_outputs(
    rows: list[dict],
    *,
    out: str,
    md: str,
) -> dict:
    # Group by tenant
    by_tenant: dict[str, list[str]] = {}
    for r in rows:
        tid = r.get("tenant_id") or ""
        if not tid:
            continue
        by_tenant.setdefault(tid, []).append(r["domain"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "resolved": sum(1 for r in rows if r.get("ok")),
        "tenants": [
            {"tenant_id": tid, "domains": sorted(doms), "domain_count": len(doms)}
            for tid, doms in sorted(by_tenant.items(), key=lambda x: x[0])
        ],
        "domains": rows,
    }
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"# wrote {out_path} (resolved={payload['resolved']}/{payload['count']})", file=sys.stderr)

    if md:
        md_path = Path(md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Entra ID OSINT",
            "",
            f"Generated: {payload['generated_at']}",
            f"Domains probed: {payload['count']}",
            f"Resolved: {payload['resolved']}",
            f"Unique tenants: {len(payload['tenants'])}",
            "",
            "## Tenants",
            "",
            "| Tenant ID | Domains |",
            "|-----------|---------|",
        ]
        for t in payload["tenants"]:
            doms = ", ".join(f"`{d}`" for d in t["domains"])
            lines.append(f"| `{t['tenant_id']}` | {doms} |")
        lines.extend(
            [
                "",
                "## Per-domain",
                "",
                "| Domain | Tenant ID | OK | HTTP |",
                "|--------|-----------|----|------|",
            ]
        )
        for r in rows:
            lines.append(
                f"| `{r['domain']}` | `{r.get('tenant_id') or ''}` | "
                f"{'yes' if r.get('ok') else 'no'} | {r.get('http_status') or ''} |"
            )
        lines.append("")
        lines.append(
            "Source: `https://login.microsoftonline.com/<domain>/v2.0/.well-known/openid-configuration` "
            "→ `issuer` tenant GUID. Raw JSON in `workspace/raw/entra-osint/`."
        )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"# wrote {md_path}", file=sys.stderr)

    return payload


def cmd_from_domains(args: argparse.Namespace) -> int:
    domains = list(args.domains or [])
    domains.extend(_read_domains(args.paths or []))
    # dedupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for d in domains:
        n = _normalize_domain(d)
        if n and n not in seen and _valid_domain(n):
            seen.add(n)
            ordered.append(n)
    if not ordered:
        print("no domains provided", file=sys.stderr)
        return 2
    rows = [lookup_tenant(d) for d in ordered]
    if args.out:
        payload = _write_outputs(rows, out=args.out, md=args.md or "")
        print(json.dumps({"resolved": payload["resolved"], "out": args.out}))
    else:
        print(json.dumps({"count": len(rows), "domains": rows}, indent=2))
    return 0


def cmd_from_inventory(args: argparse.Namespace) -> int:
    path = Path(args.inventory)
    if not path.is_file():
        print(f"inventory not found: {path}", file=sys.stderr)
        print("Run domain-enum merge first → workspace/domain-inventory.json", file=sys.stderr)
        return 2
    domains = _domains_from_inventory(path)
    if not domains:
        print(f"no domains in inventory: {path}", file=sys.stderr)
        return 2
    print(f"# probing {len(domains)} domains from {path}", file=sys.stderr)
    rows = [lookup_tenant(d) for d in domains]
    out = args.out or "workspace/entra-tenants.json"
    md = args.md or "findings/entra-osint.md"
    payload = _write_outputs(rows, out=out, md=md)
    print(json.dumps({"resolved": payload["resolved"], "tenants": len(payload["tenants"]), "out": out}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="entra_osint.py",
        description="Entra ID public OSINT — tenant ID via OpenID configuration.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tenant", help="Single domain → tenant ID")
    t.add_argument("domain", nargs="+")
    t.set_defaults(func=cmd_tenant)

    fd = sub.add_parser("from-domains", help="Domain list / file → tenant map")
    fd.add_argument("domains", nargs="*")
    fd.add_argument("-f", "--file", action="append", dest="paths", default=[])
    fd.add_argument("--out", default="")
    fd.add_argument("--md", default="")
    fd.set_defaults(func=cmd_from_domains)

    fi = sub.add_parser("from-inventory", help="domain-enum inventory JSON → tenants")
    fi.add_argument("inventory", nargs="?", default="workspace/domain-inventory.json")
    fi.add_argument("--out", default="workspace/entra-tenants.json")
    fi.add_argument("--md", default="findings/entra-osint.md")
    fi.set_defaults(func=cmd_from_inventory)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
