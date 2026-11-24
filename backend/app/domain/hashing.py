from __future__ import annotations

import hashlib
import json

from app.domain.geometry import Atom


def canonical_job_hash(
    *,
    atoms: tuple[Atom, ...],
    method: str,
    basis: str,
    charge: int,
    multiplicity: int,
) -> str:
    payload = {
        "method": method.strip().upper(),
        "basis": basis.strip().lower(),
        "charge": int(charge),
        "multiplicity": int(multiplicity),
        "atoms": [
            {
                "s": atom.symbol,
                "x": f"{atom.x:.8f}",
                "y": f"{atom.y:.8f}",
                "z": f"{atom.z:.8f}",
            }
            for atom in atoms
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
