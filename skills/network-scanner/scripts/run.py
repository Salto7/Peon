#!/usr/bin/env python3
from skill_entry import main, run_binary


def _run() -> int:
    return run_binary(
        "nmap",
        default_for_target=lambda t: f"nmap -Pn --top-ports 1000 -sV {t}",
    )


if __name__ == "__main__":
    main(_run)
