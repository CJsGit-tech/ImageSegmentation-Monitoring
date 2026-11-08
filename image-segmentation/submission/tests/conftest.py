"""Lets the tests import the scripts, which import each other as plain sibling modules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
