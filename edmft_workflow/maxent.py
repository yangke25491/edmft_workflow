from __future__ import annotations

from pathlib import Path
import re
import shutil
import numpy as np

from .checks import convergence_report, format_convergence
from .utils import WorkflowError, require_file, safe_prepare_dir, run_stage


def _sig_key(path: Path, impurity: int) -> int | None:
    m = re.fullmatch(rf"sig\.inp\.(\d+)\.{impurity}", path.name)
    return int(m.group(1)) if m else None


def select_self_energies(dmft_dir: Path, impurity: int, nlast: int) -> list[Path]:
    found: list[tuple[int, Path]] = []
    for p in dmft_dir.glob(f"sig.inp.*.{impurity}"):
        k = _sig_key(p, impurity)
        if k is not None and p.stat().st_size > 0:
            found.append((k, p))
    found.sort(key=lambda x: x[0])
    if len(found) < nlast:
        raise WorkflowError(
            f"Need {nlast} converged self-energy files for impurity {impurity}, "
            f"but found only {len(found)}"
        )
    return [p for _, p in found[-nlast:]]


def validate_same_grid(files: list[Path]) -> None:
    ref = None
    for path in files:
        data = np.loadtxt(path, comments="#")
        if data.ndim != 2 or data.shape[1] < 3:
            raise WorkflowError(f"Unexpected self-energy shape in {path}: {data.shape}")
        if ref is None:
            ref = data[:, 0].copy()
        elif len(ref) != data.shape[0] or not np.allclose(ref, data[:, 0], rtol=1e-10, atol=1e-12):
            raise WorkflowError(f"Matsubara grids differ: {files[0]} vs {path}")


def render_maxent_params(cfg) -> str:
    defaults = {
        "statistics": "fermi", "Ntau": 300, "L": 20.0, "x0": 0.005,
        "bwdth": 0.004, "Nw": 300, "gwidth": 30.0, "idg": 1,
        "deltag": 0.004, "Asteps": 4000, "alpha0": 1000,
        "min_ratio": 0.001, "iflat": 1, "Nitt": 1000, "Nr": 0, "Nf": 40,
    }
    user = cfg.section("maxent_params")
    defaults.update(user)
    lines = ["params={"]
    for key, value in defaults.items():
        if isinstance(value, str):
            lines.append(f"    {key!r}: {value!r},")
        else:
            lines.append(f"    {key!r}: {value},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def prepare_maxent(cfg, force: bool = False) -> Path:
    report = convergence_report(
        cfg.dmft_dir,
        max_dn=float(cfg.get("convergence.max_dn", 5e-3)),
        drift_tol=float(cfg.get("convergence.outer_drift", 5e-3)),
    )
    print(format_convergence(report))
    if not report["pass"] and bool(cfg.get("convergence.block_postprocess", True)):
        raise WorkflowError(
            "DMFT convergence checks did not pass. Continue run_dmft.py or set "
            "convergence.block_postprocess=false to override."
        )

    out = safe_prepare_dir(cfg.work_root / "maxent", force=force)
    impurity = int(cfg.get("maxent.impurity", 1))
    nlast = int(cfg.get("maxent.average_last", 5))
    files = select_self_energies(cfg.dmft_dir, impurity, nlast)
    validate_same_grid(files)
    local_files = []
    for src in files:
        dst = out / src.name
        shutil.copy2(src, dst)
        local_files.append(dst)
    (out / "maxent_params.dat").write_text(render_maxent_params(cfg), encoding="utf-8")
    print("Selected self-energies:")
    for p in local_files:
        print(f"  {p.name}")
    return out


def run_maxent(cfg, force: bool = False) -> Path:
    out = prepare_maxent(cfg, force=force)
    savg = str(cfg.get("commands.saverage", "saverage.py"))
    maxent = str(cfg.get("commands.maxent", "maxent_run.py"))
    files = sorted(out.glob("sig.inp.*.*"), key=lambda p: p.name)
    run_stage(cfg, [savg, *[p.name for p in files], "-o", "Sig.average"], cwd=out,
              log=out / "saverage.log")
    require_file(out / "Sig.average")
    python = cfg.get("environment.python")
    if python:
        cmd = [str(python), maxent, "Sig.average"]
    else:
        cmd = [maxent, "Sig.average"]
    run_stage(cfg, cmd, cwd=out, log=out / "maxent.log")
    require_file(out / "Sig.out")
    print(f"MaxEnt complete: {out / 'Sig.out'}")
    return out
