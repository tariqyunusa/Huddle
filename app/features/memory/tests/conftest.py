import sys
from pathlib import Path


# The pytest console script can put its entry-point directory, rather than the
# working directory, on sys.path. Ensure the application package is importable.
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
