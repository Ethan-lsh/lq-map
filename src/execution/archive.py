"""The one output layout: `summary.csv` and `raw_data.zip` (typed JSONL records).

A (case, sample, repeat, variant) key is unique; duplicates are rejected. Archives from
older raw schemas are never merged: a completed batch replaces them with a warning, and
newer schemas are rejected. A failed batch leaves the existing archive untouched.
"""

import csv
import json
import sys
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Self
from zipfile import ZIP_DEFLATED, ZipFile

from versions import RAW_SCHEMA_VERSION

SUMMARY_COLUMNS = (
    "name",
    "status",
    "objective",
    "execution_lcycles",
    "execution_us",
    "exchange_count",
    "exchange_depth",
    "movement_volume",
    "relocation_count",
    "total_request_wait",
    "msc_utilization",
    "provisioned_area",
    "spacetime_volume",
    "spacetime_volume_us",
    "active_volume",
    "peak_active_tiles",
    "active_utilization",
    "msc_pool_size",
    "planning_seconds",
)


def summary_fields(status: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Summary columns after `name`: the run status, then the selected metrics."""
    return {"status": status, **{k: metrics[k] for k in SUMMARY_COLUMNS[2:]}}


def case_label(case_id: str | None) -> str:
    return case_id or "default"


class RawDataWriter:
    """Append runs to one output root and derive its final summary from raw records."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "raw_data.zip"
        self.keys: set[tuple] = set()
        self.groups: dict[tuple, dict] = {}

    def __enter__(self) -> Self:
        self.directory.mkdir(parents=True, exist_ok=True)
        entries = {p.name for p in self.directory.iterdir()}
        if entries not in (set(), {"summary.csv", "raw_data.zip"}):
            raise ValueError("output must be empty or contain only summary.csv and raw_data.zip")
        # Work in temporary files; rejected additions cannot erase existing results.
        self.temporary = TemporaryDirectory(prefix=".pending-", dir=self.directory)
        self.pending = Path(self.temporary.name)
        self.archive = ZipFile(self.pending / "raw_data.zip", "x", compression=ZIP_DEFLATED)
        self.stream = self.archive.open("raw_data.jsonl", "w", force_zip64=True)
        try:
            self._copy_previous()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def _copy_previous(self) -> None:
        """Re-validate and carry over records from an existing archive.

        An archive from an older raw schema is not merged: it is replaced by this batch,
        and only when the batch completes (the pending files replace it on exit).
        """
        if not self.path.exists():
            return
        with ZipFile(self.path) as previous:
            if previous.namelist() != ["raw_data.jsonl"]:
                raise ValueError("unsupported raw archive layout")
            with previous.open("raw_data.jsonl") as records:
                for position, line in enumerate(records):
                    record = json.loads(line)
                    schema = record.get("raw_schema_version", RAW_SCHEMA_VERSION)
                    if position == 0 and isinstance(schema, int) and schema < RAW_SCHEMA_VERSION:
                        print(
                            f"mscmap: {self.path} uses raw schema {schema}; it is replaced "
                            f"by raw schema {RAW_SCHEMA_VERSION} results if this batch "
                            "completes (never merged)",
                            file=sys.stderr,
                        )
                        return
                    self.write(record)

    def check_new(self, case_id: str | None, sample: int, repeat: int, names: list[str]) -> None:
        case_id = case_label(case_id)
        for name in names:
            if (case_id, sample, repeat, name) in self.keys:
                raise ValueError(
                    f"duplicate result: case={case_id}, sample={sample}, "
                    f"repeat={repeat}, policy={name}; choose another case ID or repeat"
                )

    def write(self, record: dict) -> None:
        data = {"raw_schema_version": RAW_SCHEMA_VERSION, **record}
        if data["raw_schema_version"] != RAW_SCHEMA_VERSION:
            raise ValueError(
                f"{self.path} has raw schema {data['raw_schema_version']} records; this "
                f"version writes only raw schema {RAW_SCHEMA_VERSION}; choose a new --output "
                "directory"
            )
        if data["type"] == "run":
            self._add_run(data)
        self.stream.write((json.dumps(data, sort_keys=True) + "\n").encode())

    def _add_run(self, data: dict) -> None:
        case_id, name = case_label(data.get("case_id")), data["name"]
        sample, repeat, metrics = data["sample"], data["repeat"], data["metrics"]
        self.check_new(case_id, sample, repeat, [name])
        row = {
            "case_id": case_id,
            "sample": sample,
            "name": name,
            **summary_fields(metrics["status"], metrics),
        }
        config = {k: v for k, v in data["config"].items() if k != "environment"}
        stable = {k: v for k, v in row.items() if k != "planning_seconds"}
        group = self.groups.setdefault(
            (case_id, sample, name), {"config": config, "stable": stable, "row": row, "timings": []}
        )
        if config != group["config"] or stable != group["stable"]:
            raise ValueError(f"changed input or outcome for {case_id}/{name}; use a new case ID")
        group["timings"].append(metrics["planning_seconds"])
        self.keys.add((case_id, sample, repeat, name))

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.stream.close()
            self.archive.close()
            if exc_type is None:
                rows = [
                    {**g["row"], "planning_seconds": median(g["timings"])}
                    for g in self.groups.values()
                ]
                write_summary(rows, self.pending / "summary.csv")
                (self.pending / "raw_data.zip").replace(self.path)
                (self.pending / "summary.csv").replace(self.directory / "summary.csv")
        finally:
            self.archive.close()
            self.temporary.cleanup()


def write_summary(rows: list[dict], path: Path) -> None:
    """Write performance outcomes; detailed diagnostics stay in the raw archive."""
    columns = ["case_id", "sample", *SUMMARY_COLUMNS]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def read_runs(archive: Path):
    """Yield the `run` records of a raw archive."""
    with ZipFile(archive) as data, data.open("raw_data.jsonl") as records:
        for line in records:
            record = json.loads(line)
            if record["type"] == "run":
                yield record
