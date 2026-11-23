"""PyInstaller entry point for the same application as python -m chalkak."""

import sys
from pathlib import Path

# Windows starts the source launcher by absolute path at login.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chalkak.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
