from __future__ import annotations

from pathlib import Path
import re
import shutil

from .checks import validate_band_outputs
from .utils import (
    WorkflowError, count_klist_points, patch_indmfl, require_file,
    run_stage, safe_prepare_dir,
)


def resolve_klist_source(cfg) -> Path:
    raw = str(cfg.require("band.klist_source"))
    if raw.startswith("@wien:"):
        root = cfg.get("environment.wienroot")
        if not root:
            raise WorkflowError("band.klist_source uses @wien: but environment.wienroot is unset")
        return Path(str(root)) / "SRC_templates" / raw.split(":", 1)[1]
    return Path(raw).expanduser().resolve()


def prepare_band(cfg, force: bool = False) -> Path:
    case = cfg.case
    source = cfg.work_root / "onreal"
    if not source.exists():
        raise WorkflowError("Real-axis DOS directory does not exist. Run the DOS stage first.")
    out = safe_prepare_dir(cfg.work_root / "band", force=force)
    dmft_copy = str(cfg.get("commands.dmft_copy", "dmft_copy.py"))
    run_stage(cfg, [dmft_copy, str(source)], cwd=out, log=out / "dmft_copy.log")

    sig = cfg.work_root / "maxent" / "Sig.out"
    require_file(sig)
    shutil.copy2(sig, out / "sig.inp")

    ksrc = resolve_klist_source(cfg)
    require_file(ksrc)
    shutil.copy2(ksrc, out / f"{case}.klist_band")

    indmfl = out / f"{case}.indmfl"
    require_file(indmfl)
    patch_indmfl(
        indmfl,
        matsubara=0,
        nomega=int(cfg.get("band.nomega", 200)),
        wmin=float(cfg.get("band.wmin", -6.0)),
        wmax=float(cfg.get("band.wmax", 6.0)),
    )
    expected = count_klist_points(out / f"{case}.klist_band")
    print(f"Band path contains {expected} k points")
    return out


def _max_finished_kpoint(log: Path) -> int | None:
    if not log.exists():
        return None
    vals = [int(x) for x in re.findall(r"Finished k-point number\s+(\d+)", log.read_text(errors="ignore"))]
    return max(vals) if vals else None


def run_band(cfg, force: bool = False) -> Path:
    case = cfg.case
    out = prepare_band(cfg, force=force)
    xdmft = str(cfg.get("commands.x_dmft", "x_dmft.py"))
    expected = count_klist_points(out / f"{case}.klist_band")

    for name in [f"{case}.vector", f"{case}.energy", "eigvals.dat"]:
        p = out / name
        if p.exists():
            p.unlink()

    run_stage(cfg, [xdmft, "lapw1", "--band"], cwd=out, log=out / "lapw1_band.log")
    require_file(out / f"{case}.vector")
    require_file(out / f"{case}.energy")
    lapw1_def = out / "lapw1.def"
    if lapw1_def.exists() and f"{case}.klist_band" not in lapw1_def.read_text(errors="ignore"):
        raise WorkflowError(f"lapw1.def does not reference {case}.klist_band")

    run_stage(cfg, [xdmft, "dmftp"], cwd=out, log=out / "dmftp.log")
    require_file(out / "eigvals.dat")

    finished = _max_finished_kpoint(out / "dmftp.log")
    if finished is not None and finished != expected:
        raise WorkflowError(
            f"dmftp processed only {finished} k points, but {expected} are present in {case}.klist_band"
        )

    result = validate_band_outputs(out, case)
    if not result["ok"]:
        raise WorkflowError(
            "Band sanity check failed: "
            f"klist={result['expected']} numkpt={result['numkpt']} "
            f"tot-k={result['totk']} eigvals_blocks={result['eigvals_blocks']}"
        )
    print(
        "Band sanity check: PASS | "
        f"klist={result['expected']} eigvals={result['eigvals_blocks']} "
        f"numkpt={result['numkpt']} tot-k={result['totk']}"
    )
    return out
