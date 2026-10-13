"""Demand-aware logical-patch remapping and MSC scheduling on a tiled architecture.

Version constants and the command-line entry point, `python3 src/main.py <command>`
(or `mscmap <command>` after `pip install -e .`): `run` executes configured experiment
variants; `sweep`, `benchmark`, `compare`, and `plot` are the experiment drivers
implemented in `workload_sweep`, `qasm_benchmark`, `puremagic_compare`, and
`plot_active_series`. All of them write the one output layout of `experiments`.
"""

import argparse
import json
import sys
from pathlib import Path

__version__ = "0.3.0"
SCHEMA_VERSION = 3
MODEL_VERSION = "tile-swap-v3"


def main() -> int:
    # Deferred: modules import the version constants above from this module.
    import plot_active_series
    import puremagic_compare
    import qasm_benchmark
    import workload_sweep

    parser = argparse.ArgumentParser(prog="mscmap")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run configured experiment variants")
    _add_run_arguments(run_parser)
    run_parser.set_defaults(execute=_execute_run, parser=run_parser)
    for name, module in (
        ("sweep", workload_sweep),
        ("benchmark", qasm_benchmark),
        ("compare", puremagic_compare),
        ("plot", plot_active_series),
    ):
        summary = module.__doc__.split("\n\n")[0].rstrip(".")
        command = commands.add_parser(
            name, help=summary, description=module.__doc__,
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


def _add_run_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--variant", help="run only this named variant from the same config")
    command.add_argument("--case-id", help="case label in the shared output (default: config stem)")
    command.add_argument("--repeat", type=int, default=1, help="positive timing repeat number")
    command.add_argument(
        "--samples", type=int, default=1, help="stochastic cultivation samples (0..N-1)"
    )


def _execute_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from experiments import load_document, run

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


if __name__ == "__main__":
    raise SystemExit(main())
