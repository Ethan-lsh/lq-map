"""Generated tile layouts for the experiment commands (`sweep`, `benchmark`)."""

from math import ceil

Layout = tuple[list[str], dict[str, list[int]], dict[str, list[int]]]  # tiles, placement, mscs


def brick_geometry(data_qubits: int, msc_count: int, slot_rows: int, block_every: int) -> Layout:
    """Brick tile layout: in row r, column c is a slot unless (c + r) % 3 == 2. Slots are
    directly adjacent along rows, one junction tile apart elsewhere, and every bus tile
    borders slots on both axes (L-shaped CX contacts). The top and bottom rows hold the
    C slots; `slot_rows` rows between them hold data. `block_every` k > 0 blocks every
    k-th interior bus tile (row-major) to reduce ancilla space."""
    width = 3 * ceil((ceil(data_qubits / slot_rows) + 1) / 2)  # >= one spare slot per row
    height = slot_rows + 2
    tiles, bus_index = [], 0
    for r in range(height):
        row = []
        for c in range(width):
            if (c + r) % 3 != 2:
                row.append("C" if r in (0, height - 1) else "S")
            elif 0 < r < height - 1 and block_every and bus_index % block_every == 0:
                row.append(".")
                bus_index += 1
            else:
                row.append("B")
                bus_index += 0 < r < height - 1
        tiles.append("".join(row))
    slots = [(r, c) for r in range(1, height - 1) for c in range(width) if tiles[r][c] == "S"]
    top = [(0, c) for c in range(width) if tiles[0][c] == "C"]
    bottom = [(height - 1, c) for c in reversed(range(width)) if tiles[-1][c] == "C"]
    if msc_count > len(top) + len(bottom):
        raise ValueError("more MSC resources than cultivation slots")
    # Spread the pool from opposite corners so fixed capacity is distributed.
    counts = ((msc_count + 1) // 2, msc_count // 2)
    chosen = [row[j * len(row) // k] for row, k in zip((top, bottom), counts) for j in range(k)]
    per_row = ceil(data_qubits / slot_rows)
    rows = [[t for t in slots if t[0] == r] for r in range(1, height - 1)]
    placement_tiles = [t for row in rows for t in row[:per_row]][:data_qubits]
    placement = {f"q{i}": list(t) for i, t in enumerate(placement_tiles)}
    return tiles, placement, {f"m{i}": list(t) for i, t in enumerate(chosen)}
