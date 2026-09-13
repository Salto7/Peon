"""DNS helpers: dig, dnsx mass resolve, related-TLD permutation."""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lib.apex import public_suffixes


def related_tld_suffixes(*, min_left_parents: int = 10) -> list[str]:
    """PSL-derived suffixes suitable for same-label TLD permutation.

    Includes single-label ICANN suffixes plus registry-style two-label forms
    (co.uk, com.au) whose left label appears often enough on the PSL.
    """
    from collections import Counter

    all_suffixes = public_suffixes()
    one_label = {s for s in all_suffixes if "." not in s}

    left_parent_count: Counter[str] = Counter()
    for sfx in all_suffixes:
        parts = sfx.split(".")
        if len(parts) == 2 and parts[1] in one_label:
            left_parent_count[parts[0]] += 1

    registry_lefts = {
        left
        for left, n in left_parent_count.items()
        if n >= min_left_parents and left.isascii() and left.isalnum()
    }

    out: list[str] = []
    for sfx in all_suffixes:
        parts = sfx.split(".")
        if len(parts) == 1:
            if parts[0].isascii() and parts[0].isalnum() and 2 <= len(parts[0]) <= 24:
                out.append(sfx)
            continue
        if len(parts) == 2 and parts[0] in registry_lefts and parts[1] in one_label:
            out.append(sfx)
    return out


def resolves(domain: str) -> bool:
    for qtype in ("NS", "A", "AAAA"):
        proc = subprocess.run(
            ["dig", "+short", "+time=2", "+tries=1", domain, qtype],
            capture_output=True,
            text=True,
        )
        if (proc.stdout or "").strip():
            return True
    return False


def reverse_ptr(ip: str) -> str:
    proc = subprocess.run(
        ["dig", "+short", "-x", ip],
        capture_output=True,
        text=True,
    )
    lines = [
        ln.strip().rstrip(".")
        for ln in (proc.stdout or "").splitlines()
        if ln.strip()
    ]
    return lines[0] if lines else ""


def expand_targets(token: str, *, max_hosts: int = 256) -> list[str]:
    token = token.strip()
    if not token or token.startswith("#"):
        return []
    try:
        if "/" in token:
            net = ipaddress.ip_network(token, strict=False)
            hosts = list(net.hosts())
            if len(hosts) > max_hosts:
                hosts = hosts[:max_hosts]
            return [str(h) for h in hosts]
        ipaddress.ip_address(token)
        return [token]
    except ValueError:
        return []


def permute_tlds(labels: list[str]) -> list[str]:
    """Build `{label}.{suffix}` candidates for each registrable label."""
    suffixes = related_tld_suffixes()
    seen: set[str] = set()
    out: list[str] = []
    for raw in labels:
        label = (raw or "").strip().lower().strip(".")
        if not label or "." in label:
            continue
        for sfx in suffixes:
            cand = f"{label}.{sfx}"
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def find_dnsx() -> str | None:
    found = shutil.which("dnsx")
    if found:
        return found
    # local go-install path used in repo tests
    here = Path(__file__).resolve()
    for root in (here.parents[4], here.parents[3], Path.cwd()):
        cand = root / ".tools" / "dnsx"
        if cand.is_file():
            return str(cand)
    return None


def dnsx_resolve(
    domains: list[str],
    *,
    dnsx_bin: str | None = None,
    workers: int = 50,
    timeout: int = 3,
) -> list[str]:
    """Mass-resolve with dnsx; fall back to parallel dig if dnsx missing."""
    domains = [d.strip().lower() for d in domains if (d or "").strip()]
    if not domains:
        return []
    bin_path = dnsx_bin or find_dnsx()
    if bin_path:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(domains) + "\n")
            list_path = fh.name
        try:
            proc = subprocess.run(
                [
                    bin_path,
                    "-l",
                    list_path,
                    "-silent",
                    "-a",
                    "-aaaa",
                    "-ns",
                    "-re",
                    "-t",
                    str(max(1, workers)),
                    "-timeout",
                    f"{max(1, timeout)}s",
                    "-duc",
                ],
                capture_output=True,
                text=True,
                timeout=max(180, len(domains) // 2 + 90),
            )
        except (subprocess.TimeoutExpired, OSError):
            proc = None
        finally:
            Path(list_path).unlink(missing_ok=True)
        hits: list[str] = []
        seen: set[str] = set()
        if proc is not None and (proc.returncode == 0 or (proc.stdout or "").strip()):
            for ln in (proc.stdout or "").splitlines():
                # dnsx -re lines look like: domain [A] [8.8.8.8]
                host = ln.split()[0].strip().lower().rstrip(".") if ln.strip() else ""
                if host and host not in seen:
                    seen.add(host)
                    hits.append(host)
            if hits or (proc.returncode == 0):
                return sorted(hits)
        # if dnsx failed, fall through to dig

    hits = []
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 64))) as pool:
        futs = {pool.submit(resolves, d): d for d in domains}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok and d not in seen:
                seen.add(d)
                hits.append(d)
    return sorted(hits)


def resolve_related_tlds(label: str, *, workers: int = 50) -> list[str]:
    cands = permute_tlds([label])
    return dnsx_resolve(cands, workers=workers)
