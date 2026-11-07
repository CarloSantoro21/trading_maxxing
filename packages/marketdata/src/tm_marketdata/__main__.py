"""Permite `python -m tm_marketdata ...`."""

import sys

from tm_marketdata.cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
