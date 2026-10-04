"""GlueRunner — execute the body below ``# --- LLM_BODY ---``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from context import PackContext
from runners.runner_base import PackRunnerBase

LLM_BODY_MARKER = "# --- LLM_BODY ---"


class GlueRunner(PackRunnerBase):
    """Execute the body below ``# --- LLM_BODY ---`` with optional imports."""

    def __init__(
        self,
        script_path: str | Path,
        *,
        imports: str = "",
        context: PackContext | None = None,
    ) -> None:
        super().__init__(context)
        self.script_path = Path(script_path)
        self.imports = imports

    def run(self) -> int:
        text = self.script_path.read_text(encoding="utf-8")
        if LLM_BODY_MARKER not in text:
            print(
                f"missing {LLM_BODY_MARKER} in {self.script_path.name}",
                file=sys.stderr,
            )
            return 2
        body = text.split(LLM_BODY_MARKER, 1)[1].lstrip("\n")
        if not body.strip():
            print("empty LLM body — nothing to run", file=sys.stderr)
            return 2
        ns: dict[str, Any] = {
            "__name__": "__main__",
            "__file__": str(self.script_path),
        }
        if self.imports.strip():
            exec(self.imports, ns, ns)  # noqa: S102 — intentional pack glue surface
        exec(body, ns, ns)  # noqa: S102
        return 0
