from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
from typing import Mapping, Iterable

from .utils import WorkflowError, require_file


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
        "refreshed_utc": None,
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


def refresh_stage_manifest(stage_dir: Path, extra_prepared: Iterable[Path] | None = None) -> Path | None:
    """Refresh hashes after the user has inspected/edited native stage inputs.

    This is called when a standalone PBS file is generated, so the manifest
    records the exact control files that will be used by the manually submitted
    job rather than only their first prepare-time versions.
    """
    manifest = stage_dir / "manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WorkflowError(f"Could not read stage manifest {manifest}: {exc}") from exc

    sources = data.get("sources", {})
    for name, rec in list(sources.items()):
        path = Path(str(rec.get("path", "")))
        sources[name] = _record(path)

    prepared_records = []
    seen: set[str] = set()
    for rec in data.get("prepared", []):
        path = Path(str(rec.get("path", "")))
        key = str(path.resolve())
        if key in seen:
            continue
        prepared_records.append(_record(path))
        seen.add(key)
    for path in extra_prepared or []:
        p = Path(path)
        key = str(p.resolve())
        if key not in seen:
            prepared_records.append(_record(p))
            seen.add(key)

    data["sources"] = sources
    data["prepared"] = prepared_records
    data["refreshed_utc"] = datetime.now(timezone.utc).isoformat()
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_stage_manifest(stage_dir: Path) -> tuple[bool, str]:
    """Verify that current files still match the most recently refreshed manifest."""
    manifest = stage_dir / "manifest.json"
    if not manifest.exists():
        return False, f"missing {manifest}"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"unreadable manifest: {exc}"

    mismatches: list[str] = []
    records = list(data.get("sources", {}).values()) + list(data.get("prepared", []))
    for rec in records:
        path = Path(str(rec.get("path", "")))
        if not path.is_file() or path.stat().st_size == 0:
            mismatches.append(f"missing:{path.name}")
            continue
        if path.stat().st_size != int(rec.get("size", -1)):
            mismatches.append(f"size:{path.name}")
            continue
        if sha256_file(path) != rec.get("sha256"):
            mismatches.append(f"sha256:{path.name}")
    if mismatches:
        return False, ", ".join(mismatches)
    stamp = data.get("refreshed_utc") or data.get("created_utc") or "unknown"
    return True, f"matches manifest snapshot {stamp}"
