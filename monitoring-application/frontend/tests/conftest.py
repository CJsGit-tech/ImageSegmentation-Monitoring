"""Lets the tests import core.*, as the app does with PYTHONPATH=/app in its container."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
