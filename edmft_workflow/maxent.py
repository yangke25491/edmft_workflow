from __future__ import annotations

from pathlib import Path
import re
import shutil
import numpy as np

from .checks import convergence_report, format_convergence
from .fermi import copy_fermi_snapshot
from .provenance import write_stage_manifest
from .utils import WorkflowError, require_file, safe_prepare_dir, run_edmft_helper


OFFICIAL_MAXENT_PARAMS = """params={'statistics': 'fermi', # fermi/bose
    'Ntau'      : 400,     # Number of time points
    'L'         : 30.0,    # cutoff frequency on real axis
    'x0'        : 0.01,    # low energy cut-off
    'bwdth'     : 0.004,   # smoothing width
    'Nw'        : 450,     # number of frequency points on real axis
    'gwidth'    : 2*15.0,  # width of gaussian
    'idg'       : 1,       # error scheme: idg=1 -> sigma=deltag ; idg=0 -> sigma=deltag*G(tau)
    'deltag'    : 0.01,    # error
    'Asteps'    : 4000,    # annealing steps
    'alpha0'    : 1000,    # starting alpha
    'min_ratio' : 0.001,   # stopping ratio
    'iflat'     : 1,       # 0 constant model; 1 gaussian; 2 model.dat
    'Nitt'      : 500,     # maximum number of outside iterations
    'Nr'        : 0,       # number of smoothing runs
    'Nf'        : 40,      # high-frequency points used in inverse Fourier
    'SymCum'    : True,    # symmetrize local cumulant when possible
    }
"""


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
            f"Need {nlast} self-energy files for impurity {impurity}, but found only {len(found)}"
        )
    return [p for _, p in found[-nlast:]]


def validate_same_grid(files: list[Path]) -> None:
    ref = None
    for path in files:
        try:
            data = np.loadtxt(path, comments="#")
        except ValueError as exc:
            raise WorkflowError(f"Could not parse numerical self-energy data in {path}: {exc}") from exc
        if data.ndim != 2 or data.shape[1] < 3:
            raise WorkflowError(f"Unexpected self-energy shape in {path}: {data.shape}")
        if ref is None:
            ref = data[:, 0].copy()
        elif len(ref) != data.shape[0] or not np.allclose(ref, data[:, 0], rtol=1e-10, atol=1e-12):
            raise WorkflowError(f"Matsubara grids differ: {files[0]} vs {path}")


def active_maxent_baths(path: Path) -> int:
    """Count nonzero complex self-energy channels exactly as maxent_run.py does."""
    data = np.loadtxt(path, comments="#").T
    nonzero_columns = 0
    for row in data[1:]:
        if np.sum(np.abs(row)) > 0:
            nonzero_columns += 1
    return nonzero_columns // 2


def prepare_maxent(cfg, force: bool = False) -> Path:
    """Prepare official MaxEnt inputs without running the expensive continuation."""
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
    impurity = int(cfg.get("post.impurity", cfg.get("maxent.impurity", 1)))
    nlast = int(cfg.get("post.average_last", cfg.get("maxent.average_last", 3)))
    files = select_self_energies(cfg.dmft_dir, impurity, nlast)
    validate_same_grid(files)

    local_files: list[Path] = []
    for src in files:
        dst = out / src.name
        shutil.copy2(src, dst)
        local_files.append(dst)

    selected = out / "selected_sigmas.txt"
    selected.write_text("\n".join(p.name for p in local_files) + "\n", encoding="utf-8")

    run_edmft_helper(
        cfg,
        "saverage.py",
        [*[p.name for p in local_files], "-o", "sig.inpx"],
        cwd=out,
        log=out / "saverage.log",
    )
    siginpx = require_file(out / "sig.inpx")
    nb = active_maxent_baths(siginpx)

    inputs = cfg.root_dir / "inputs"
    supplied = inputs / "maxent_params.dat"
    target = out / "maxent_params.dat"
    sources = {f"self_energy_{i+1}": p for i, p in enumerate(files)}
    if supplied.exists():
        require_file(supplied)
        shutil.copy2(supplied, target)
        origin = str(supplied)
        sources["maxent_params"] = supplied
    else:
        target.write_text(OFFICIAL_MAXENT_PARAMS, encoding="utf-8")
        origin = "current upstream maxent_run.py default template"

    # MaxEnt does not recompute the chemical potential.  Keep a local snapshot
    # of the DMFT EF used by the selected self-energies so later DOS/band stages
    # can detect stale combinations.  Missing EF is warning-only by design.
    ef_snapshot = out / "fermi_level.snapshot"
    ef_value = copy_fermi_snapshot(cfg.dmft_dir / "EF.dat", ef_snapshot)

    prepared = [selected, siginpx, target]
    if ef_value is not None:
        prepared.append(ef_snapshot)

    manifest = write_stage_manifest(
        out,
        "maxent",
        sources=sources,
        prepared=prepared,
    )

    print("Selected self-energies:")
    for p in local_files:
        print(f"  {p.name}")
    print(f"Averaged Matsubara self-energy : {siginpx}")
    print(f"Active MaxEnt baths/channels   : {nb}")
    print(f"Useful MaxEnt MPI ranks        : <= {max(1, nb)}")
    print(f"MaxEnt parameter file          : {target}")
    print(f"Parameter source               : {origin}")
    if ef_value is None:
        print("WARNING: dmft/EF.dat is missing or unreadable; no Fermi-level snapshot was recorded.")
    else:
        print(f"DMFT Fermi-level snapshot      : {ef_snapshot} ({ef_value:.12f} eV)")
    print(f"Provenance manifest            : {manifest}")
    print("Inspect sig.inpx and maxent_params.dat before generating the PBS job.")
    return out
