#!/usr/bin/env python3
from skill_entry import main, run_cli
from entra_osint import main as entra_main

if __name__ == "__main__":
    main(
        lambda: run_cli(
            entra_main,
            usage=(
                "usage: run.py '<entra_osint subcommand and args>'\n"
                "  tenant | from-domains | from-inventory\n"
                "example: run.py 'tenant acme.com'"
            ),
        )
    )
