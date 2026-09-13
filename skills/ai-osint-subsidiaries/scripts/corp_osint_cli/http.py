"""Minimal HTTP helpers — stdlib only."""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_UA = "peontester-corp-osint/1.1 (OSINT research; corp-osint@localhost.invalid)"


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    retries: int = 2,
) -> str:
    hdrs = {
        "User-Agent": _UA,
        "Accept": "application/json,text/html,*/*",
    }
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                charset = "utf-8"
                ctype = resp.headers.get_content_charset()
                if ctype:
                    charset = ctype
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            last_exc = RuntimeError(f"HTTP {exc.code} for {url}: {body[:200]}")
            if exc.code == 429 and attempt < retries:
                time.sleep(65)
                continue
            raise last_exc from exc
        except urllib.error.URLError as exc:
            last_exc = RuntimeError(f"request failed for {url}: {exc}")
            if attempt < retries:
                time.sleep(2)
                continue
            raise last_exc from exc
    if last_exc:
        raise last_exc
    return ""


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> Any:
    text = fetch(url, headers=headers, timeout=timeout)
    if not text:
        return {}
    return json.loads(text)


def wikidata_sparql(query: str, *, timeout: int = 90) -> dict[str, Any]:
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode(
        {"query": query, "format": "json"}
    )
    data = fetch_json(
        url,
        headers={"Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise RuntimeError("invalid SPARQL JSON response")
    return data
