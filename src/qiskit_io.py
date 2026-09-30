"""Optional Qiskit front end: OpenQASM or a `QuantumCircuit` -> the mscmap circuit format.

Qiskit only parses the circuit and decomposes it into the model's Clifford+T gate set;
arbitrary rotations are approximated by Qiskit's synthesis, which therefore sets the T
count. Dependencies, placement, and scheduling stay in mscmap. Logical qubits are
named `q<i>` by their index in the circuit. Final measurements are removed and
barriers ignored; mid-circuit measurement, reset, and classical control are rejected.
"""

from pathlib import Path
from typing import Any

BASIS = ("h", "s", "sdg", "x", "z", "t", "tdg", "cx", "cz")
IGNORED = frozenset({"barrier"})
OPTIMIZATION_LEVEL = 1


def _qiskit():
    try:
        import qiskit
    except ImportError as exc:
        raise ValueError("QASM circuits need Qiskit; install mscmap[qiskit]") from exc
    return qiskit


def qiskit_version() -> str | None:
    try:
        return _qiskit().__version__
    except ValueError:
        return None


def load_qasm(path: Path) -> dict[str, Any]:
    """Parse an OpenQASM 2 or 3 file and convert it with `from_qiskit`."""
    qiskit = _qiskit()
    text = path.read_text()
    try:
        if text.lstrip().startswith("OPENQASM 3"):
            circuit = qiskit.qasm3.loads(text)
        else:
            legacy = qiskit.qasm2.LEGACY_CUSTOM_INSTRUCTIONS
            circuit = qiskit.qasm2.loads(text, custom_instructions=legacy)
    except (qiskit.exceptions.QiskitError, ImportError) as exc:
        raise ValueError(f"cannot parse {path}: {exc}") from exc
    return from_qiskit(circuit)


def from_qiskit(circuit) -> dict[str, Any]:
    """Decompose a `QuantumCircuit` into Clifford+T gates in program order."""
    qiskit = _qiskit()
    compiled = qiskit.transpile(
        circuit.remove_final_measurements(inplace=False),
        basis_gates=list(BASIS),
        optimization_level=OPTIMIZATION_LEVEL,
        seed_transpiler=0,
    )
    names = [f"q{i}" for i in range(compiled.num_qubits)]
    gates = []
    for instruction in compiled.data:
        kind = instruction.operation.name
        if kind in IGNORED:
            continue
        if kind not in BASIS or instruction.clbits:
            raise ValueError(
                f"unsupported operation {kind!r}: mid-circuit measurement, reset, and "
                "classical control are not modeled"
            )
        qubits = [names[compiled.find_bit(q).index] for q in instruction.qubits]
        gates.append({"id": f"g{len(gates):05d}", "kind": kind.upper(), "qubits": qubits})
    return {"qubits": names, "gates": gates}
