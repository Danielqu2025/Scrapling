"""Build portable Windows packages for example apps under examples/."""

from packager.build import build_example, discover_examples, list_examples
from packager.scaffold import init_portable_files

__all__ = ["build_example", "discover_examples", "list_examples", "init_portable_files"]
