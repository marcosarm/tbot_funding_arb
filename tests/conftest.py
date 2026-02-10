import sys
from pathlib import Path

# Allow `import btengine` without requiring an editable install.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

