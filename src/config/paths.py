"""Repository locations used by the experiment commands."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "experiment.yaml"
DATA_ROOT = REPO_ROOT / "data"
