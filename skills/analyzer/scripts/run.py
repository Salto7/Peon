#!/usr/bin/env python3
from skill_entry import main, run_report
from report import build_report

if __name__ == "__main__":
    main(lambda: run_report(build_report))
