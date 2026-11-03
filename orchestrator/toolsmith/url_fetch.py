"""Ground an authoring prompt in whatever the operator actually linked.

An operator prompt that names a URL ("add https://github.com/x/y") is
usually pointing at the tool's real home. Two shapes of grounding share the
fetch machinery below:

- ``install_instructions_for_prompt`` — just the install-ish heading, for
  drafting a catalog *tool* YAML (install steps only, kept narrow on
  purpose).
- ``reference_material_for_prompt`` — the whole document, for drafting a
  *skill* (usage patterns, flags, examples — a skill has to document how to
  actually use the thing, not just how to install it).

Both are best-effort and silent: network failures, a host with nothing
recognizable, or no URL at all all fall back to the unmodified prompt, same
as before either existed.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?(?:[/#?].*)?$"
)
_README_CANDIDATES = ("README.md", "readme.md", "Readme.md", "README.rst", "README")
_HEADING_RE = re.compile(r"(?m)^(#{1,4})[ \t]+(.+?)[ \t]*$")
_FENCE_RE = re.compile(r"(?m)^(```|~~~).*$")
_INSTALL_WORDS = re.compile(r"\b(install(?:ation|ing)?|getting started|quick ?start|setup)\b", re.I)
_MAX_FETCH_BYTES = 200_000
_MAX_SECTION_CHARS = 3_000
_MAX_REFERENCE_CHARS = 6_000
_TIMEOUT_S = 8.0


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;:") if m else ""


def _github_readme_urls(url: str) -> list[str]:
    m = _GITHUB_REPO_RE.match(url)
    if not m:
        return []
    owner, repo = m.group("owner"), m.group("repo")
    # HEAD resolves to the repo's default branch — no need to know its name.
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{name}"
        for name in _README_CANDIDATES
    ]


def _fetch_text(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT_S) as client:
        resp = client.get(url, headers={"User-Agent": "Peon-Toolsmith/1.0"})
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "text" not in content_type and "markdown" not in content_type and content_type:
        raise ValueError(f"not text content: {content_type!r}")
    return resp.text[:_MAX_FETCH_BYTES]


def _install_section(doc: str) -> str:
    """First heading whose title mentions install/setup, up to the next
    heading of the same or shallower level."""
    fence_lines = {m.start() for m in _FENCE_RE.finditer(doc)}
    in_fence = False
    fenced_spans: list[tuple[int, int]] = []
    fence_start = 0
    for pos in sorted(fence_lines):
        if not in_fence:
            fence_start = pos
            in_fence = True
        else:
            fenced_spans.append((fence_start, pos))
            in_fence = False

    def _in_fence(pos: int) -> bool:
        return any(a <= pos <= b for a, b in fenced_spans)

    headings = [h for h in _HEADING_RE.finditer(doc) if not _in_fence(h.start())]
    for i, h in enumerate(headings):
        if not _INSTALL_WORDS.search(h.group(2)):
            continue
        level = len(h.group(1))
        start = h.end()
        end = len(doc)
        for nxt in headings[i + 1 :]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        section = doc[start:end].strip()
        if section:
            return section[:_MAX_SECTION_CHARS]
    return ""


def install_instructions_for_prompt(prompt: str) -> str:
    """Real install instructions for the first URL in ``prompt``, or ``""``.

    Only GitHub repo URLs are resolved today (README fetch needs no auth
    and no repo clone) — the common case for "add this CLI" requests. Any
    other host, or a GitHub repo with no install-ish heading in its README,
    returns ``""`` so the caller's prompt is unchanged.
    """
    url = _first_url(prompt)
    if not url:
        return ""
    for candidate in _github_readme_urls(url):
        try:
            doc = _fetch_text(candidate)
        except Exception as exc:  # noqa: BLE001 — best-effort, never block drafting
            logger.debug("toolsmith install-doc fetch failed for %s: %s", candidate, exc)
            continue
        section = _install_section(doc)
        if section:
            return section
    return ""


def reference_material_for_prompt(prompt: str) -> str:
    """The first linked URL's own documentation, for skill-authoring context.

    Broader than ``install_instructions_for_prompt`` — a skill needs to
    document *usage* (flags, workflows, examples), not just how to install
    the thing. A GitHub repo URL fetches the whole README; any other
    http(s) URL is fetched directly (a docs page, a blog post, ...). Same
    resilience contract: any failure anywhere returns ``""``.
    """
    url = _first_url(prompt)
    if not url:
        return ""
    readme_candidates = _github_readme_urls(url)
    for candidate in readme_candidates or [url]:
        try:
            doc = _fetch_text(candidate)
        except Exception as exc:  # noqa: BLE001 — best-effort, never block drafting
            logger.debug("toolsmith reference fetch failed for %s: %s", candidate, exc)
            continue
        if doc.strip():
            return doc.strip()[:_MAX_REFERENCE_CHARS]
    return ""
