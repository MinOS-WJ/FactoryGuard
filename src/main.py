"""FactoryGuard desktop entry point.

Run this file from the repository checkout with ``python src/main.py``.
The application code lives in the sibling ``factoryguard`` package, so no
package installation or editable install is required.
"""

from __future__ import annotations

from factoryguard.cli import main


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
