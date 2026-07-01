"""
Build portable package for sh_eia.

See examples/README.md for the full packaging guide.

Quick start:

    pip install -r requirements-app.txt
    pip install -r ../requirements-portable.txt
    python ../package_app.py build sh_eia

This file remains for backward compatibility (same as package_app.py build sh_eia).
"""

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
