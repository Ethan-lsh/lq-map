"""Version constants of the package, the configuration schema, and the result formats.

Bump `MODEL_VERSION` whenever resource semantics change (execution, measurement, SWAP
planning, configurations, and documentation move together).
"""

__version__ = "0.4.0"
SCHEMA_VERSION = 3  # configuration schema
MODEL_VERSION = "tile-swap-v3"  # execution model
RAW_SCHEMA_VERSION = 8  # raw_data.jsonl records
METRICS_VERSION = "online-v7"  # RunMetrics summary
EVALUATION_VERSION = "performance-v9"  # summary.csv columns and comparisons
