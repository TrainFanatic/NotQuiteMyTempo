import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "rushingOrDragging"))

from library import render  # noqa: E402

needs_fluidsynth = pytest.mark.skipif(render.available() is not None, reason=str(render.available()))
