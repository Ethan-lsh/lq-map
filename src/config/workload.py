"""Seeded synthetic circuits; T targets are fixed relative to the supplied placement."""

import random
from math import ceil

from algorithm.model import Tile

WORKLOAD_FAMILIES = ("localized", "migrating", "alternating", "distributed", "random")
WORKLOAD_DEFAULTS = {
    "family": "alternating",
    "requests": 4,
    "burst": 1,
    "cx_per_burst": 0,
    "cx_span": "global",
    "dependency": "independent",
}


def workload(spec: dict, placement: dict[str, Tile], seed: int) -> dict:
    """Seeded synthetic circuit; T targets are fixed relative to the supplied placement.

    Each burst issues `cx_per_burst` CX gates, then `burst` T requests each followed
    by H. `dependency` adds control edges: `serial` chains T requests, `burst` makes
    every burst wait for the previous one. Per-qubit order is added on import.
    """
    spec = workload_spec(spec, len(placement))
    count, burst, cx_count = spec["requests"], spec["burst"], spec["cx_per_burst"]
    names = sorted(placement, key=lambda q: (placement[q], q))
    rng = random.Random(seed)
    gates: list[dict] = []
    previous_burst: list[str] = []
    last_t: list[str] = []
    for b in range(ceil(count / burst)):
        barrier = previous_burst if spec["dependency"] == "burst" else []
        current: list[str] = []
        for k in range(cx_count):
            first, second = _cx_pair(names, rng, spec["cx_span"])
            gate_id = f"cx{b:04d}_{k}"
            gates.append(
                {"id": gate_id, "kind": "CX", "qubits": [first, second], "predecessors": barrier}
            )
            current.append(gate_id)
        for i in range(b * burst, min(count, (b + 1) * burst)):
            target = _t_target(spec["family"], names, i, count, rng)
            predecessors = barrier + (last_t if spec["dependency"] == "serial" else [])
            gates.append(
                {"id": f"t{i:04d}", "kind": "T", "qubits": [target], "predecessors": predecessors}
            )
            gates.append({"id": f"h{i:04d}", "kind": "H", "qubits": [target]})
            last_t = [f"t{i:04d}"]
            current.append(f"h{i:04d}")
        previous_burst = current
    return {"qubits": sorted(placement), "gates": gates}


def workload_spec(spec: dict, data_qubits: int) -> dict:
    """Workload options merged over the defaults, with actionable validation errors."""
    unknown = set(spec) - set(WORKLOAD_DEFAULTS)
    if unknown:
        raise ValueError(f"unsupported workload options: {sorted(unknown)}")
    spec = {**WORKLOAD_DEFAULTS, **spec}
    count, burst, cx_count = spec["requests"], spec["burst"], spec["cx_per_burst"]
    if any(type(v) is not int for v in (count, burst, cx_count)) or min(count, burst) < 1:
        raise ValueError("workload requests/burst must be positive and cx_per_burst >= 0")
    if cx_count < 0 or (cx_count and data_qubits < 2):
        raise ValueError("CX workloads need cx_per_burst >= 0 and at least two data qubits")
    if spec["family"] not in WORKLOAD_FAMILIES:
        raise ValueError(f"workload family must be one of {', '.join(WORKLOAD_FAMILIES)}")
    if spec["cx_span"] not in {"local", "global"}:
        raise ValueError("workload cx_span must be local or global")
    if spec["dependency"] not in {"independent", "serial", "burst"}:
        raise ValueError("workload dependency must be independent, serial, or burst")
    return spec


def _t_target(family: str, names: list[str], i: int, count: int, rng: random.Random) -> str:
    if family == "localized":
        return names[0]
    if family == "migrating":
        return names[min(len(names) - 1, i * len(names) // count)]
    if family == "alternating":
        return names[0 if i % 2 == 0 else -1]
    if family == "distributed":
        return names[i % len(names)]
    return rng.choice(names)


def _cx_pair(names: list[str], rng: random.Random, span: str) -> tuple[str, str]:
    """`local` partners are within two positions in initial-placement order."""
    index = rng.randrange(len(names))
    if span == "local":
        others = [j for j in range(index - 2, index + 3) if 0 <= j < len(names) and j != index]
    else:
        others = [j for j in range(len(names)) if j != index]
    return names[index], names[rng.choice(others)]
