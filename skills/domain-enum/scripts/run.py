#!/usr/bin/env python3
from skill_entry import main, run_cli
from domain_enum import main as domain_main

if __name__ == "__main__":
    main(
        lambda: run_cli(
            domain_main,
            usage=(
                "usage: run.py '<domain_enum subcommand and args>'\n"
                "  extract | reverse-whois | related-tlds | urlscan | dnslytics\n"
                "  rdap | quien | harvester | reverse-ranges | merge | from-corp | "
                "subdomains | pipeline\n"
                "example: run.py 'extract --json'\n"
                "example: run.py 'subdomains --from-corp --out workspace/subdomains.txt'"
            ),
        )
    )
