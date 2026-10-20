"""FactoryGuard package entry point.

The normal checkout entry point is ``python src/main.py``.  This module also
supports ``python src/factoryguard/main.py`` for compatibility with existing
launch shortcuts.
"""

from __future__ import annotations

if __package__ in {None, ""}:  # direct execution: python src/factoryguard/main.py
    import sys
    from pathlib import Path

    _SRC = Path(__file__).resolve().parents[1]
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from factoryguard.cli import main
else:
    from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
