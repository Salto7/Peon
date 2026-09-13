#!/usr/bin/env python3
from skill_entry import main, run_cli
from cli import main as cli_main

if __name__ == "__main__":
    main(
        lambda: run_cli(
            cli_main,
            usage=(
                "usage: run.py 'workflow|sec|pipeline|prompt|merge-ai …'\n"
                'example: run.py \'workflow "ACME" --workspace workspace\''
            ),
        )
    )
