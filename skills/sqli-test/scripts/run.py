#!/usr/bin/env python3
from skill_entry import main, run_binary


def _run() -> int:
    return run_binary(
        "sqlmap",
        default_for_target=lambda t: (
            f"sqlmap -u {t} --batch --level=1 --risk=1 --output-dir=workspace/sqlmap"
        ),
    )


if __name__ == "__main__":
    main(_run)
