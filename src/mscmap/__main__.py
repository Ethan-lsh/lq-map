"""One command for single-variant and multi-variant experiments."""

import argparse
import json
import sys
from pathlib import Path

from .experiments import load_document, run


def main() -> int:
    parser = argparse.ArgumentParser(prog="mscmap")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="run configured experiment variants")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--variant", help="run only this named variant from the same config")
    command.add_argument("--case-id", help="case label in the shared output (default: config stem)")
    command.add_argument("--repeat", type=int, default=1, help="positive repeat number")
    args = parser.parse_args()
    try:
        raw = load_document(args.config)
        rows = run(
            raw,
            args.config.parent,
            args.output,
            variant=args.variant,
            case_id=args.case_id if args.case_id is not None else args.config.stem,
            repeat=args.repeat,
        )
        print(json.dumps(rows, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"mscmap: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
