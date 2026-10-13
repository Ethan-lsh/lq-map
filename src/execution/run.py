"""Run the variants of one configuration file.

Command `run` of `main.py`: every variant of `--config` on identical inputs; the output
directory receives `summary.csv` and `raw_data.zip` (shared across repeats and cases).
"""

import argparse
import json
from pathlib import Path

from config.loader import load_document
from execution.engine import run


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", help="run only this named variant from the same config")
    parser.add_argument("--case-id", help="case label in the shared output (default: config stem)")
    parser.add_argument("--repeat", type=int, default=1, help="positive timing repeat number")
    parser.add_argument(
        "--samples", type=int, default=1, help="stochastic cultivation samples (0..N-1)"
    )


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
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
