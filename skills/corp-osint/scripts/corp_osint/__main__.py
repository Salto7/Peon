"""python -m corp_osint → single entrypoint."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import main

if __name__ == "__main__":
    main()
