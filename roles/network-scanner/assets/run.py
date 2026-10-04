#!/usr/bin/env python3
"""Thin nmap entry: one ``-oX`` scan. Analyzer reads the raw XML later."""

from pack_entry import main, run_binary


def _run() -> int:
    return run_binary(
        "nmap",
        default_for_target=lambda t: (
            f"nmap -Pn --top-ports 100 -sT "
            f"-oX workspace/scan-{t.replace('/', '_')}.xml {t}"
        ),
    )


if __name__ == "__main__":
    main(_run)
