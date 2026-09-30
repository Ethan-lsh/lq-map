"""Demand-aware logical-patch remapping and MSC scheduling on a tiled architecture.

Version constants and the one command, `python3 -m mscmap run`, for single-variant
and multi-variant experiments.
"""

import argparse
import json
import sys
from pathlib import Path

__version__ = "0.3.0"
SCHEMA_VERSION = 3
MODEL_VERSION = "tile-swap-v1"


# TODO: 실험 케이스를 일반 양자회로로 변경 (Flow-based, puremagic 논문 참조)
def main() -> int:
    # Deferred: modules import the version constants above from this module.
    from experiments import load_document, run

    args = _parser().parse_args()
    try:
        raw = load_document(args.config)
        rows = run(
            raw,
            args.config.parent,
            args.output,
            variant=args.variant,
            case_id=args.case_id if args.case_id is not None else args.config.stem,
            repeat=args.repeat,
            samples=args.samples,
        )
        print(json.dumps(rows, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"mscmap: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mscmap")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="run configured experiment variants")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--variant", help="run only this named variant from the same config")
    command.add_argument("--case-id", help="case label in the shared output (default: config stem)")
    command.add_argument("--repeat", type=int, default=1, help="positive timing repeat number")
    command.add_argument(
        "--samples", type=int, default=1, help="stochastic cultivation samples (0..N-1)"
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
