#!/usr/bin/env python3
from skill_entry import main, run_binary


def _run() -> int:
    return run_binary(
        "httpx",
        default_for_target=lambda t: (
            f"httpx -u {t if '://' in t else f'https://{t}'} -silent"
        ),
    )


if __name__ == "__main__":
    main(_run)
