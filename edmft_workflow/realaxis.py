from __future__ import annotations

from pathlib import Path
import shutil

from .utils import (
    WorkflowError, copy_case_files, patch_indmfl, require_file,
    run_stage, safe_prepare_dir,
)


def _copy_wien_potentials(cfg, out: Path) -> None:
    """Make the post-processing directory independent of its parent directory."""
    case = cfg.case
    # LAPW1/DMFT need these potentials locally. dmft_copy.py does not reliably
    # carry them into every post-processing directory.
    copy_case_files(cfg.dmft_dir, out, case, ["vsp", "vns"], required=True)
    copy_case_files(
        cfg.dmft_dir, out, case,
        ["vspup", "vspdn", "vnsup", "vnsdn"], required=False,
    )


def prepare_dos(cfg, force: bool = False) -> Path:
    case = cfg.case
    source = cfg.dmft_dir
    out = safe_prepare_dir(cfg.dmft_dir / "onreal", force=force)
    dmft_copy = str(cfg.get("commands.dmft_copy", "dmft_copy.py"))
    run_stage(cfg, [dmft_copy, str(source)], cwd=out, log=out / "dmft_copy.log")
    _copy_wien_potentials(cfg, out)

    sig = cfg.dmft_dir / "maxent" / "Sig.out"
    require_file(sig)
    shutil.copy2(sig, out / "sig.inp")

    indmfl = out / f"{case}.indmfl"
    require_file(indmfl)
    shutil.copy2(indmfl, out / f"{case}.indmfl.matsubara")
    patch_indmfl(
        indmfl,
        matsubara=0,
        nomega=int(cfg.get("dos.nomega", 200)),
        wmin=float(cfg.get("dos.wmin", -3.0)),
        wmax=float(cfg.get("dos.wmax", 1.0)),
    )
    return out


def run_dos(cfg, force: bool = False) -> Path:
    case = cfg.case
    out = prepare_dos(cfg, force=force)
    wien_x = str(cfg.get("commands.wien_x", "x"))
    xdmft = str(cfg.get("commands.x_dmft", "x_dmft.py"))

    run_stage(cfg, [wien_x, "lapw0", "-f", case], cwd=out, log=out / "lapw0.log")
    run_stage(cfg, [xdmft, "lapw1"], cwd=out, log=out / "lapw1.log")
    require_file(out / f"{case}.vector")
    require_file(out / f"{case}.energy")

    run_stage(cfg, [xdmft, "dmft1"], cwd=out, log=out / "dmft1.log")
    marker_ok = False
    for p in [out / "dmft1.log", out / f"{case}.outputdmf1"]:
        if p.exists() and "DMFT1 END" in p.read_text(errors="ignore"):
            marker_ok = True
            break
    if not marker_ok:
        raise WorkflowError("dmft1 did not report 'DMFT1 END'")

    for name in [f"{case}.cdos", f"{case}.gc1", f"{case}.dlt1", f"{case}.Eimp1"]:
        require_file(out / name)
    print(f"Real-axis DOS complete: {out}")
    return out
