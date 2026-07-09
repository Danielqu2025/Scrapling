"""Build portable zip + exe for noc_gb_monitor."""

from __future__ import annotations

import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from packager.build import build_example


def main() -> None:
    build_example(Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
