from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from typing import Mapping, Iterable

from .utils import require_file


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return SHA256 for a scientific input/output file."""
    require_file(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _record(path: Path) -> dict:
    require_file(path)
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_stage_manifest(
    stage_dir: Path,
    stage: str,
    sources: Mapping[str, Path] | None = None,
    prepared: Iterable[Path] | None = None,
) -> Path:
    """Write a small provenance manifest for reproducibility.

    Only explicitly supplied scientific inputs and prepared control files are
    hashed. Large transient vector/energy files are intentionally excluded.
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "stage": stage,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {},
        "prepared": [],
    }
    for name, path in (sources or {}).items():
        data["sources"][str(name)] = _record(Path(path))
    for path in prepared or []:
        data["prepared"].append(_record(Path(path)))

    out = stage_dir / "manifest.json"
    out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
