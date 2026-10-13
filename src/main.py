"""Demand-aware logical-patch remapping and MSC scheduling on a tiled architecture.

Command-line entry point, `python3 src/main.py <command>` (or `mscmap <command>` after
`pip install -e .`). Source packages: `algorithm` (the scheduling model and policies),
`config` (option values and inputs), `execution` (running variants and the output
layout), and `analysis` (comparison commands). Each command module exposes
`add_arguments(parser)` and `execute(args, parser)`.
"""

import argparse
import sys

from analysis import benchmark, compare, plot, sweep
from execution import run

COMMANDS = (
    ("run", run),
    ("sweep", sweep),
    ("benchmark", benchmark),
    ("compare", compare),
    ("plot", plot),
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="mscmap")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, module in COMMANDS:
        summary = module.__doc__.split("\n\n")[0].rstrip(".")
        command = commands.add_parser(
            name,
            help=summary,
            description=module.__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        module.add_arguments(command)
        command.set_defaults(execute=module.execute, parser=command)
    args = parser.parse_args()
    try:
        args.execute(args, args.parser)
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"mscmap {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
