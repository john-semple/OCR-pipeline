"""pytest config — make `src/` importable as `src.*` from tests/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))