#!/usr/bin/env python3
"""domain-enum CLI — domain inventory + dnsx subdomain enumeration.

Subcommands:
  extract | reverse-whois | related-tlds | urlscan | dnslytics
  rdap | quien | harvester | reverse-ranges | merge | subdomains
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.apex import (  # noqa: E402
    HAS_TLDEXTRACT,
    HOST_RE,
    collect,
    sld_label,
    to_apex,
)
from lib.dns import expand_targets, resolve_related_tlds, reverse_ptr  # noqa: E402
from lib.http import fetch, fetch_json  # noqa: E402
from lib.io import load_corp_handoff_details, read_tokens  # noqa: E402

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_DEFAULT_SUBDOMAIN_WORDLIST = _SCRIPTS.parent / "assets" / "wordlists" / "subdomains.txt"


def _corp_details(args: argparse.Namespace) -> dict[str, object]:
    cached = getattr(args, "_corp_handoff_cache", None)
    if isinstance(cached, dict):
        return cached
    if not getattr(args, "from_corp", False):
        details: dict[str, object] = {"names": [], "domains": [], "excluded_low_confidence": []}
        setattr(args, "_corp_handoff_cache", details)
        return details
    details = load_corp_handoff_details(getattr(args, "workspace", "workspace") or "workspace")
    setattr(args, "_corp_handoff_cache", details)
    return details


def _corp_names(args: argparse.Namespace) -> list[str]:
    return list(_corp_details(args).get("names", []))


def _corp_domains(args: argparse.Namespace) -> list[str]:
    return list(_corp_details(args).get("domains", []))


def _add_from_corp(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from-corp",
        action="store_true",
        help="Read entity names / domains from workspace/corp-entities.json (or seed files)",
    )
    parser.add_argument(
        "--workspace",
        default="workspace",
        help="Directory that contains corp-entities.json (default: workspace)",
    )


def _load_wordlist(args: argparse.Namespace) -> list[str]:
    path = Path(args.wordlist) if getattr(args, "wordlist", "") else _DEFAULT_SUBDOMAIN_WORDLIST
    if not path.is_file():
        raise ValueError(f"missing wordlist: {path}")
    words = [
        line.strip().lower()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # Optional ad-hoc additions on top of the file-backed wordlist.
    words.extend(w.strip().lower() for w in (args.words or []) if w.strip())
    return sorted(dict.fromkeys(words))


def _parse_dnsx_hosts(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        host = line.strip().lower()
        if not host or host.startswith("#") or " " in host:
            continue
        if not HOST_RE.fullmatch(host):
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out


def _worker_count(value: int, *, fallback: int = 8, cap: int = 64) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(1, min(cap, n))


def _run_dnsx_bruteforce(
    *,
    domains: list[str],
    words: list[str],
    threads: int,
    resolvers: str,
    timeout: str,
) -> list[str]:
    if not shutil.which("dnsx"):
        raise RuntimeError(
            "dnsx not on PATH — check tools/catalog provisioning"
        )
    with NamedTemporaryFile("w", encoding="utf-8", delete=True) as domf, NamedTemporaryFile(
        "w", encoding="utf-8", delete=True
    ) as wordf:
        domf.write("\n".join(domains) + "\n")
        domf.flush()
        wordf.write("\n".join(words) + "\n")
        wordf.flush()
        cmd = [
            "dnsx",
            "-silent",
            "-d",
            domf.name,
            "-w",
            wordf.name,
            "-t",
            str(max(1, int(threads))),
            "-timeout",
            timeout or "3s",
            "-duc",
        ]
        if (resolvers or "").strip():
            cmd.extend(["-r", resolvers.strip()])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"dnsx failed (exit={proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:500]}"
            )
        return _parse_dnsx_hosts(proc.stdout or "")


def cmd_extract(args: argparse.Namespace) -> int:
    texts: list[str] = []
    if not sys.stdin.isatty():
        texts.append(sys.stdin.read())
    domains = collect(
        texts,
        [Path(p) for p in (args.paths or [])],
        keep_hosts=args.keep_hosts,
        exclude_suffixes=set(args.exclude_suffix or []),
    )
    if args.as_json:
        print(
            json.dumps(
                {
                    "domains": domains,
                    "count": len(domains),
                    "mode": "hosts" if args.keep_hosts else "etld1",
                    "tldextract": HAS_TLDEXTRACT,
                },
                indent=2,
            )
        )
    else:
        for d in domains:
            print(d)
    return 0


# ── subdomains (dnsx bruteforce) ──────────────────────────────────────────


def cmd_subdomains(args: argparse.Namespace) -> int:
    corp_domains = _corp_domains(args) if getattr(args, "from_corp", False) else []
    domains = [
        to_apex(d) or d.lower()
        for d in read_tokens(args.domains or [], read_stdin=not corp_domains)
    ]
    domains.extend(corp_domains)
    domains = sorted(dict.fromkeys(d for d in domains if d))
    if not domains:
        print(
            "usage: domain_enum.py subdomains <domain>... [--from-corp]",
            file=sys.stderr,
        )
        return 2
    try:
        words = _load_wordlist(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not words:
        print("empty wordlist", file=sys.stderr)
        return 2
    print(
        f"# dnsx bruteforce domains={len(domains)} words={len(words)} threads={args.threads}",
        file=sys.stderr,
    )
    try:
        hosts = _run_dnsx_bruteforce(
            domains=domains,
            words=words,
            threads=args.threads,
            resolvers=args.resolvers,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(hosts) + ("\n" if hosts else ""), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domains": domains,
        "word_count": len(words),
        "count": len(hosts),
        "out": str(out_path),
    }
    if args.json_out:
        jpath = Path(args.json_out)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(
            json.dumps({**payload, "subdomains": hosts}, indent=2) + "\n",
            encoding="utf-8",
        )
        payload["json_out"] = str(jpath)
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Subdomain inventory",
            "",
            f"Generated: {payload['generated_at']}",
            f"Base domains: {len(domains)}",
            f"Wordlist size: {len(words)}",
            f"Resolved subdomains: {len(hosts)}",
            "",
            "| Subdomain |",
            "|---|",
        ]
        for host in hosts[:1000]:
            lines.append(f"| `{host}` |")
        if len(hosts) > 1000:
            lines.append(f"| … {len(hosts) - 1000} more |")
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"# wrote {md}", file=sys.stderr)
        payload["md"] = str(md)
    print(f"# wrote {out_path} ({len(hosts)} subdomains)", file=sys.stderr)
    print(json.dumps(payload, indent=2))
    return 0


# ── reverse-whois ────────────────────────────────────────────────────────


def _reverse_whoxy(keyword: str) -> list[str]:
    key = (os.environ.get("WHOXY_API_KEY") or "").strip()
    if not key:
        return []
    q = urllib.parse.urlencode(
        {"key": key, "reverse": "whois", "keyword": keyword, "mode": "mini"}
    )
    try:
        data = fetch_json(f"https://api.whoxy.com/?{q}")
    except Exception:
        return []
    out: list[str] = []
    for row in data.get("search_result") or []:
        d = (row.get("domain_name") or row.get("domain") or "").strip().lower()
        if d:
            out.append(d)
    return out


def _reverse_viewdns(keyword: str) -> list[str]:
    html = fetch(f"https://viewdns.info/reversewhois/?q={urllib.parse.quote(keyword)}")
    domains: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"<td[^>]*>\s*([a-z0-9.-]+\.[a-z]{2,})\s*</td>", html, re.I
    ):
        d = m.group(1).strip().lower()
        if d in {"domain name", "domain"} or "viewdns" in d:
            continue
        if d not in seen and HOST_RE.fullmatch(d):
            seen.add(d)
            domains.append(d)
    if not domains:
        for m in HOST_RE.finditer(html):
            d = m.group(0).lower()
            if any(x in d for x in ("viewdns", "google", "gstatic", "cloudflare")):
                continue
            if d not in seen:
                seen.add(d)
                domains.append(d)
    return domains


def _reverse_lookup_keyword(keyword: str) -> tuple[str, str, list[str], str]:
    """Reverse whois lookup for one keyword.

    Returns: (keyword, source, results, error)
    source is one of: whoxy, viewdns, none.
    """
    results = _reverse_whoxy(keyword)
    if results:
        return keyword, "whoxy", results, ""
    try:
        view = _reverse_viewdns(keyword)
        return keyword, "viewdns", view, ""
    except Exception as exc:
        return keyword, "none", [], str(exc)


def cmd_reverse_whois(args: argparse.Namespace) -> int:
    corp_names = _corp_names(args)
    keys = read_tokens(args.keywords or [], read_stdin=not corp_names) + corp_names
    if not keys:
        print(
            "usage: domain_enum.py reverse-whois <org-or-email>... [--from-corp]",
            file=sys.stderr,
        )
        return 2
    workers = _worker_count(getattr(args, "workers", 8), fallback=8, cap=32)
    seen: set[str] = set()
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for kw in keys:
            print(f"# reverse_whois keyword={kw!r}", file=sys.stderr)
            futures[ex.submit(_reverse_lookup_keyword, kw)] = kw
        for fut in as_completed(futures):
            kw = futures[fut]
            try:
                _kw, source, results, err = fut.result()
            except Exception as exc:
                print(f"# reverse_whois failed for {kw!r}: {exc}", file=sys.stderr)
                continue
            if err:
                print(f"# viewdns failed for {kw!r}: {err}", file=sys.stderr)
            if source in {"whoxy", "viewdns"}:
                print(f"# source={source} keyword={kw!r} count={len(results)}", file=sys.stderr)
            for d in results:
                if d not in seen:
                    seen.add(d)
                    print(d)
    return 0


# ── related-tlds ─────────────────────────────────────────────────────────


def cmd_related_tlds(args: argparse.Namespace) -> int:
    corp_domains = _corp_domains(args)
    seeds = read_tokens(args.seeds or [], read_stdin=not corp_domains) + corp_domains
    if not seeds:
        print(
            "usage: domain_enum.py related-tlds <seed-domain>... [--from-corp]",
            file=sys.stderr,
        )
        return 2
    try:
        from lib.dns import related_tld_suffixes

        n_suffix = len(related_tld_suffixes())
    except Exception as exc:
        print(f"# tldextract PSL unavailable: {exc}", file=sys.stderr)
        return 1
    print(
        f"# related_tlds using filtered tldextract PSL ({n_suffix} suffixes)",
        file=sys.stderr,
    )
    seen: set[str] = set()
    for seed in seeds:
        label = sld_label(seed)
        if not label:
            print(f"# skip invalid seed {seed!r}", file=sys.stderr)
            continue
        print(f"# related_tlds label={label!r} from {seed}", file=sys.stderr)
        for cand in resolve_related_tlds(label, workers=args.workers):
            if cand in seen:
                continue
            seen.add(cand)
            print(cand)
    return 0


# ── urlscan ──────────────────────────────────────────────────────────────


def cmd_urlscan(args: argparse.Namespace) -> int:
    domain = (args.domain or "").strip().lower()
    if not domain:
        print("usage: domain_enum.py urlscan <domain>", file=sys.stderr)
        return 2
    key = (os.environ.get("URLSCAN_API_KEY") or "").strip()
    if not key:
        print("URLSCAN_API_KEY not set — skip or configure secrets.", file=sys.stderr)
        return 1
    q = urllib.parse.quote(f"domain:{domain}")
    url = f"https://urlscan.io/api/v1/search/?q={q}&size=50"
    raw = fetch(url, headers={"API-Key": key})
    data = json.loads(raw)
    domains = collect([raw], [], keep_hosts=False)
    print(
        json.dumps(
            {
                "query": domain,
                "domains": domains,
                "raw_count": len(data.get("results") or []),
                "results": data.get("results") or [],
            },
            indent=2,
        )
    )
    return 0


# ── dnslytics ────────────────────────────────────────────────────────────


def cmd_dnslytics(args: argparse.Namespace) -> int:
    seed = (args.seed or "").strip().lower().removeprefix("www.")
    if not seed:
        print("usage: domain_enum.py dnslytics <basename-or-domain>", file=sys.stderr)
        return 2
    apex = to_apex(seed) or seed
    base = apex.split(".")[0]
    key = (os.environ.get("DNSLYTICS_API_KEY") or "").strip()
    if key:
        q = urllib.parse.urlencode({"apikey": key, "q": base})
        try:
            data = fetch_json(
                f"https://api.dnslytics.com/v1/domainsearch?{q}", timeout=45
            )
            hits: list[str] = []
            for item in (
                data
                if isinstance(data, list)
                else (data.get("domains") or data.get("results") or [])
            ):
                if isinstance(item, str):
                    hits.append(item)
                elif isinstance(item, dict):
                    d = item.get("domain") or item.get("name") or ""
                    if d:
                        hits.append(str(d))
            print(
                json.dumps(
                    {"query": base, "source": "dnslytics_api", "domains": hits},
                    indent=2,
                )
            )
            return 0
        except Exception as exc:
            print(f"# DNSlytics API failed: {exc}", file=sys.stderr)
    search_q = f"(name:*{base}*)&d=domains"
    url = f"https://search.dnslytics.com/search?q={urllib.parse.quote(search_q)}"
    print(
        json.dumps(
            {
                "query": base,
                "source": "dnslytics_search_url",
                "search_url": url,
                "domains": [],
                "note": (
                    "No DNSLYTICS_API_KEY or API failed — open search_url. "
                    "Pipe saved results through: domain_enum.py extract"
                ),
            },
            indent=2,
        )
    )
    print(url, file=sys.stderr)
    return 0


# ── rdap ─────────────────────────────────────────────────────────────────


def cmd_rdap(args: argparse.Namespace) -> int:
    domains = [to_apex(d) or d.lower() for d in read_tokens(args.domains or [])]
    domains = [d for d in domains if d]
    if not domains:
        print("usage: domain_enum.py rdap <domain>...", file=sys.stderr)
        return 2
    workers = _worker_count(getattr(args, "workers", 12), fallback=12, cap=64)

    def _rdap_one(domain: str) -> dict:
        try:
            data = fetch_json(
                f"https://rdap.org/domain/{domain}",
                headers={"Accept": "application/rdap+json, application/json"},
                timeout=45,
            )
            return {"domain": domain, "ok": True, "ldhName": data.get("ldhName"), "raw": data}
        except Exception as exc:
            return {"domain": domain, "ok": False, "error": str(exc)}

    out: list[dict] = []
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for domain in domains:
            futures[ex.submit(_rdap_one, domain)] = domain
        for fut in as_completed(futures):
            out.append(fut.result())
    out.sort(key=lambda x: x.get("domain", ""))
    print(json.dumps(out, indent=2))
    return 0


# ── quien ────────────────────────────────────────────────────────────────


def cmd_quien(args: argparse.Namespace) -> int:
    domain = (args.domain or "").strip()
    if not domain:
        print("usage: domain_enum.py quien <domain> [whois|dns|mail|tls|http|all]", file=sys.stderr)
        return 2
    mode = (args.mode or "all").strip().lower()
    cmds = {
        "whois": ["quien", "whois", domain],
        "dns": ["quien", "dns", domain],
        "mail": ["quien", "mail", domain],
        "tls": ["quien", "tls", domain],
        "http": ["quien", "http", domain],
    }
    if mode == "all":
        sequence = list(cmds.values())
    elif mode in cmds:
        sequence = [cmds[mode]]
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    if not shutil.which("quien"):
        print(
            "quien not on PATH — check tools/catalog provisioning",
            file=sys.stderr,
        )
        return 1
    rc = 0
    for cmd in sequence:
        print(f"# {' '.join(cmd)}", flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        if proc.returncode:
            rc = proc.returncode
    return rc


# ── harvester ────────────────────────────────────────────────────────────


def cmd_harvester(args: argparse.Namespace) -> int:
    domain = (args.domain or "").strip()
    if not domain:
        print("usage: domain_enum.py harvester <domain> [source]", file=sys.stderr)
        return 2
    source = (args.source or "bing,duckduckgo,yahoo").strip()
    binary = "theHarvester" if shutil.which("theHarvester") else (
        "theharvester" if shutil.which("theharvester") else ""
    )
    if not binary:
        print("theHarvester not installed — optional; skip.", file=sys.stderr)
        return 1
    cmd = [binary, "-d", domain, "-b", source]
    print(f"# {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    emails = sorted(set(_EMAIL_RE.findall(text)))
    if emails:
        print("# emails_for_reverse_whois:", file=sys.stderr)
        for e in emails:
            print(f"# email\t{e}", file=sys.stderr)
    if proc.stderr and not proc.stdout:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode if proc.returncode is not None else 1


# ── reverse-ranges ───────────────────────────────────────────────────────


def cmd_reverse_ranges(args: argparse.Namespace) -> int:
    tokens = read_tokens(args.targets or [], skip_hash=True)
    if not tokens:
        print(
            "usage: domain_enum.py reverse-ranges <ip-or-cidr>...\n"
            "# cloud ASN denylist configured in ownership filter stage",
            file=sys.stderr,
        )
        return 2
    for tok in tokens:
        for ip in expand_targets(tok):
            print(f"{ip}\t{reverse_ptr(ip)}")
    print(
        "# Filter with ASN enrichment; drop CLOUD_ASN_HINTS before promoting. "
        "Pipe PTR hosts through: domain_enum.py extract",
        file=sys.stderr,
    )
    return 0


# ── merge ────────────────────────────────────────────────────────────────


def _brand_labels(args: argparse.Namespace) -> set[str]:
    labels: set[str] = set()
    for raw in args.brand_label or []:
        tok = (raw or "").strip().lower()
        if tok:
            labels.add(tok)
    # Derive labels from seed domains (acme.com → acme).
    for s in args.seed or []:
        lab = sld_label(s)
        if lab:
            labels.add(lab.lower())
    return labels


def _matches_brand(apex: str, brands: set[str]) -> bool:
    if not brands:
        return False
    lab = (sld_label(apex) or "").lower()
    if not lab:
        return False
    if lab in brands:
        return True
    # Allow compact brand forms (acme → acme already handled via seeds).
    return any(len(b) >= 4 and (b in lab or lab in b) for b in brands)


def cmd_merge(args: argparse.Namespace) -> int:
    if getattr(args, "from_corp", False):
        args.seed = list(args.seed or []) + _corp_domains(args)
    paths = list(args.paths or [])
    if not paths and not (args.seed or []):
        print("merge needs candidate files, --seed, or --from-corp", file=sys.stderr)
        return 2
    by_domain: dict[str, dict] = {}
    for p in paths:
        path = Path(p)
        source = path.stem
        for d in collect([], [path], keep_hosts=False):
            apex = to_apex(d)
            if not apex:
                continue
            row = by_domain.setdefault(
                apex, {"domain": apex, "sources": [], "confidence": "medium"}
            )
            if source not in row["sources"]:
                row["sources"].append(source)
    for s in args.seed or []:
        apex = to_apex(s)
        if not apex:
            continue
        row = by_domain.setdefault(
            apex, {"domain": apex, "sources": [], "confidence": "high"}
        )
        if "seed" not in row["sources"]:
            row["sources"].insert(0, "seed")
        row["confidence"] = "high"

    brands = _brand_labels(args)
    for row in by_domain.values():
        src = set(row["sources"])
        if "seed" in src:
            row["confidence"] = "high"
        elif any("whois" in s for s in src):
            row["confidence"] = "high"
        elif len(src) >= 2:
            row["confidence"] = "medium"
        else:
            row["confidence"] = "low"
        # Same-label brand TLDs are in-scope medium (never demote below medium).
        if brands and _matches_brand(row["domain"], brands):
            row["brand_match"] = True
            if row["confidence"] == "low":
                row["confidence"] = "medium"
            if "brand-label" not in row["sources"]:
                row["sources"].append("brand-label")

    retained: list[dict] = []
    excluded: list[dict] = []
    drop_unrelated = bool(args.drop_unrelated)
    for row in sorted(by_domain.values(), key=lambda r: r["domain"]):
        is_seed = "seed" in row["sources"]
        brand = bool(row.get("brand_match")) or _matches_brand(row["domain"], brands)
        if drop_unrelated and brands and not is_seed and not brand:
            excluded.append(
                {
                    "domain": row["domain"],
                    "reason": "no brand-label match (likely reverse-whois/citation noise)",
                    "sources": row["sources"],
                    "confidence": row["confidence"],
                }
            )
            continue
        # Never drop seed or brand-matched rows, including medium confidence.
        retained.append(row)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(retained),
        "domains": retained,
        "excluded": excluded,
        "excluded_count": len(excluded),
        "brand_labels": sorted(brands),
        "policy": {
            "keep_medium_brand_matches": True,
            "drop_unrelated": drop_unrelated,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"# wrote {out} (retained={len(retained)} excluded={len(excluded)})",
        file=sys.stderr,
    )
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Domain inventory",
            "",
            f"Generated: {payload['generated_at']}",
            f"Retained: {len(retained)}",
            f"Excluded: {len(excluded)}",
            f"Brand labels: {', '.join(sorted(brands)) or '(none)'}",
            "",
            "| Domain | Sources | Confidence |",
            "|--------|---------|------------|",
        ]
        for r in retained:
            lines.append(
                f"| `{r['domain']}` | {', '.join(r['sources'])} | {r['confidence']} |"
            )
        if excluded:
            lines.extend(
                [
                    "",
                    "## Excluded (noise / non-brand)",
                    "",
                    "| Domain | Reason |",
                    "|--------|--------|",
                ]
            )
            for r in excluded[:200]:
                lines.append(f"| `{r['domain']}` | {r['reason']} |")
            if len(excluded) > 200:
                lines.append(f"| … | {len(excluded) - 200} more in JSON |")
        lines.append("")
        lines.append(
            "Only registrable (apex) domains. Medium-confidence brand matches are "
            "in-scope and retained. Evidence in `workspace/raw/`."
        )
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"# wrote {md}", file=sys.stderr)
    print(
        json.dumps(
            {
                "count": len(retained),
                "excluded": len(excluded),
                "out": str(out),
            }
        )
    )
    return 0


def cmd_from_corp(args: argparse.Namespace) -> int:
    details = load_corp_handoff_details(getattr(args, "workspace", "workspace") or "workspace")
    names = list(details.get("names", []))
    domains = list(details.get("domains", []))
    excluded = list(details.get("excluded_low_confidence", []))
    payload = {
        "names": names,
        "domains": domains,
        "excluded_low_confidence": excluded,
        "count_names": len(names),
        "count_domains": len(domains),
        "count_excluded_low_confidence": len(excluded),
    }
    print(json.dumps(payload, indent=2))
    return 0 if names or domains else 2


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Load corp handoff domains, then enumerate subdomains via dnsx."""
    workspace = Path(getattr(args, "workspace", "workspace") or "workspace")
    details = load_corp_handoff_details(workspace)
    names = list(details.get("names", []))
    domains = list(details.get("domains", []))
    excluded = list(details.get("excluded_low_confidence", []))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed_path = out_dir / "corp-from-enum.json"
    subdomains_out = out_dir / "subdomains.txt"
    subdomains_json = out_dir / "subdomains.json"
    payload = {
        "names": names,
        "domains": domains,
        "excluded_low_confidence": excluded,
        "count_names": len(names),
        "count_domains": len(domains),
        "count_excluded_low_confidence": len(excluded),
        "source": str(workspace / "corp-entities.json"),
    }
    seed_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if domains:
        sub_args = argparse.Namespace(
            domains=[],
            from_corp=True,
            workspace=str(workspace),
            words=args.words,
            wordlist=args.wordlist,
            threads=args.threads,
            resolvers=args.resolvers,
            timeout=args.timeout,
            out=str(subdomains_out),
            json_out=str(subdomains_json),
            md="",
        )
        rc = cmd_subdomains(sub_args)
        if rc != 0:
            return rc
        try:
            sub_payload = json.loads(subdomains_json.read_text(encoding="utf-8"))
            payload["subdomain_count"] = int(sub_payload.get("count", 0))
            payload["subdomains_out"] = str(subdomains_out)
        except Exception:
            payload["subdomain_count"] = 0
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Domain seeds from corp-osint",
            "",
            f"Entities: {len(names)}",
            f"Known apex domains: {len(domains)}",
            f"Excluded low-confidence domains: {len(excluded)}",
            f"Resolved subdomains: {payload.get('subdomain_count', 0)}",
            "",
            "| Domain |",
            "|---|",
        ]
        for domain in domains:
            lines.append(f"| `{domain}` |")
        if excluded:
            lines.extend(
                [
                    "",
                    "| Excluded Domain | Reason |",
                    "|---|---|",
                ]
            )
            for row in excluded:
                lines.append(
                    f"| `{row.get('domain', '')}` | {row.get('reason', 'low-confidence ai domain')} |"
                )
        if payload.get("subdomains_out"):
            lines.extend(
                [
                    "",
                    f"Subdomain output: `{payload['subdomains_out']}`",
                ]
            )
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"# wrote {md}", file=sys.stderr)
    print(json.dumps({**payload, "out": str(seed_path)}, indent=2))
    return 0 if names or domains else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domain_enum.py",
        description="Domain inventory + dnsx subdomain enumeration.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="URLs/JSON/text → apex domains")
    e.add_argument("paths", nargs="*")
    e.add_argument("--keep-hosts", action="store_true")
    e.add_argument("--json", action="store_true", dest="as_json")
    e.add_argument("--exclude-suffix", action="append", default=[])
    e.set_defaults(func=cmd_extract)

    rw = sub.add_parser("reverse-whois", help="Org/email → related domains")
    rw.add_argument("keywords", nargs="*")
    rw.add_argument("--workers", type=int, default=8, help="parallel keyword workers (default 8)")
    _add_from_corp(rw)
    rw.set_defaults(func=cmd_reverse_whois)

    rt = sub.add_parser(
        "related-tlds",
        help="Same SLD across Public Suffix List suffixes (tldextract + dig)",
    )
    rt.add_argument("seeds", nargs="*")
    rt.add_argument(
        "--workers",
        type=int,
        default=32,
        help="parallel dig workers (default 32)",
    )
    _add_from_corp(rt)
    rt.set_defaults(func=cmd_related_tlds)

    u = sub.add_parser("urlscan", help="urlscan.io search (needs URLSCAN_API_KEY)")
    u.add_argument("domain")
    u.set_defaults(func=cmd_urlscan)

    d = sub.add_parser("dnslytics", help="DNSlytics search / API")
    d.add_argument("seed")
    d.set_defaults(func=cmd_dnslytics)

    r = sub.add_parser("rdap", help="Bulk RDAP lookups")
    r.add_argument("domains", nargs="*")
    r.add_argument("--workers", type=int, default=12, help="parallel rdap workers (default 12)")
    r.set_defaults(func=cmd_rdap)

    q = sub.add_parser("quien", help="quien enrich (whois/dns/mail/tls/http)")
    q.add_argument("domain")
    q.add_argument("mode", nargs="?", default="all")
    q.set_defaults(func=cmd_quien)

    h = sub.add_parser("harvester", help="Optional theHarvester (emails → reverse-whois)")
    h.add_argument("domain")
    h.add_argument("source", nargs="?", default="bing,duckduckgo,yahoo")
    h.set_defaults(func=cmd_harvester)

    rr = sub.add_parser("reverse-ranges", help="dig -x for IPs/CIDRs")
    rr.add_argument("targets", nargs="*")
    rr.set_defaults(func=cmd_reverse_ranges)

    sd = sub.add_parser(
        "subdomains",
        help="Bruteforce subdomains for base domains via dnsx",
    )
    sd.add_argument("domains", nargs="*")
    sd.add_argument(
        "--words",
        action="append",
        default=[],
        help="Subdomain word(s); repeat flag to add more",
    )
    sd.add_argument(
        "--wordlist",
        default="",
        help="Path to wordlist file (one token per line). If omitted, built-in defaults are used.",
    )
    sd.add_argument("--threads", type=int, default=64)
    sd.add_argument("--resolvers", default="")
    sd.add_argument("--timeout", default="3s")
    sd.add_argument("--out", default="workspace/subdomains.txt")
    sd.add_argument("--json-out", default="workspace/subdomains.json")
    sd.add_argument("--md", default="")
    _add_from_corp(sd)
    sd.set_defaults(func=cmd_subdomains)

    m = sub.add_parser("merge", help="Merge candidate files → inventory")
    m.add_argument("paths", nargs="*")
    m.add_argument("--out", required=True)
    m.add_argument("--md", default="")
    m.add_argument("--seed", action="append", default=[])
    m.add_argument(
        "--brand-label",
        action="append",
        default=[],
        help="SLD brand token(s) to keep as in-scope (e.g. acme). "
        "Same-label related-TLD hits stay at ≥ medium confidence.",
    )
    m.add_argument(
        "--drop-unrelated",
        action="store_true",
        help="Drop candidates that are neither --seed nor --brand-label matches "
        "(filters reverse-whois/citation noise without dropping brand TLDs).",
    )
    _add_from_corp(m)
    m.set_defaults(func=cmd_merge)

    fc = sub.add_parser("from-corp", help="Print entity names + domains from corp-osint JSON")
    fc.add_argument("--workspace", default="workspace")
    fc.set_defaults(func=cmd_from_corp)

    pl = sub.add_parser(
        "pipeline",
        help="From corp/AI handoff domains, run dnsx subdomain enumeration",
    )
    pl.add_argument("--from-corp", action="store_true", default=True)
    pl.add_argument("--workspace", default="workspace")
    pl.add_argument("--out-dir", default="workspace")
    pl.add_argument("--md", default="")
    pl.add_argument("--words", action="append", default=[])
    pl.add_argument("--wordlist", default="")
    pl.add_argument("--threads", type=int, default=64)
    pl.add_argument("--resolvers", default="")
    pl.add_argument("--timeout", default="3s")
    pl.set_defaults(func=cmd_pipeline)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
