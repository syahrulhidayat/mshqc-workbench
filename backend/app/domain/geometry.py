from __future__ import annotations

import math
from dataclasses import dataclass

# All-electron non-relativistic element support (Z = 1..36, H through Kr)
ATOMIC_NUMBERS: dict[str, int] = {
    "H": 1, "He": 2,
    "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9, "Ne": 10,
    "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15, "S": 16, "Cl": 17, "Ar": 18,
    "K": 19, "Ca": 20, "Sc": 21, "Ti": 22, "V": 23, "Cr": 24, "Mn": 25, "Fe": 26,
    "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30, "Ga": 31, "Ge": 32, "As": 33, "Se": 34,
    "Br": 35, "Kr": 36,
}

SUPPORTED_ELEMENTS: frozenset[str] = frozenset(ATOMIC_NUMBERS.keys())

MAX_ATOMS = 64
MIN_INTERATOMIC_DISTANCE = 1e-4


@dataclass(frozen=True)
class Atom:
    symbol: str
    x: float
    y: float
    z: float


def parse_xyz(raw_xyz: str) -> tuple[Atom, ...]:
    lines = [line.strip() for line in raw_xyz.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("XYZ geometry cannot be empty.")

    # Support optional standard XYZ header (atom count on line 1, comment on line 2).
    if lines[0].isdigit():
        expected_count = int(lines[0])
        if len(lines) < 3:
            raise ValueError("Standard XYZ header requires a comment line and atom coordinates.")
        coord_lines = lines[2:]
        if len(coord_lines) != expected_count:
            raise ValueError(
                f"XYZ header declares {expected_count} atoms, but found {len(coord_lines)}."
            )
    else:
        coord_lines = lines

    if len(coord_lines) > MAX_ATOMS:
        raise ValueError(f"Atom count exceeds safety limit ({MAX_ATOMS}).")

    atoms: list[Atom] = []
    for idx, line in enumerate(coord_lines, start=1):
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"Line {idx}: expected 'Symbol X Y Z', got '{line}'.")

        symbol = parts[0].capitalize()
        if symbol not in SUPPORTED_ELEMENTS:
            raise ValueError(
                f"Line {idx}: unsupported element symbol '{parts[0]}' (supported: H..Kr)."
            )

        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError as exc:
            raise ValueError(f"Line {idx}: coordinates must be valid numbers.") from exc

        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise ValueError(f"Line {idx}: coordinates must be finite numbers.")

        atoms.append(Atom(symbol=symbol, x=x, y=y, z=z))

    for i in range(len(atoms)):
        for j in range(i + 1, len(atoms)):
            dist = math.dist(
                (atoms[i].x, atoms[i].y, atoms[i].z),
                (atoms[j].x, atoms[j].y, atoms[j].z),
            )
            if dist < MIN_INTERATOMIC_DISTANCE:
                raise ValueError(
                    f"Atoms {i + 1} ({atoms[i].symbol}) and {j + 1} ({atoms[j].symbol}) "
                    "overlap within 1e-4 Angstrom."
                )

    return tuple(atoms)


def total_electrons(atoms: tuple[Atom, ...], charge: int) -> int:
    nuclear_charge = sum(ATOMIC_NUMBERS[a.symbol] for a in atoms)
    electrons = nuclear_charge - charge
    if electrons <= 0:
        raise ValueError("Invalid charge: resulting electron count must be positive.")
    return electrons


def validate_spin_parity(atoms: tuple[Atom, ...], charge: int, multiplicity: int) -> None:
    electrons = total_electrons(atoms, charge)
    unpaired = multiplicity - 1
    if unpaired < 0 or unpaired > electrons:
        raise ValueError("Multiplicity is inconsistent with total electron count.")
    if (electrons - unpaired) % 2 != 0:
        raise ValueError(
            f"Spin parity mismatch: {electrons} electrons cannot form multiplicity {multiplicity}."
        )
