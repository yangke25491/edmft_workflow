from __future__ import annotations

from pathlib import Path
import shutil

from .provenance import write_stage_manifest
from .realaxis import _copy_wien_potentials, _prepare_real_axis_indmfl
from .utils import WorkflowError, count_klist_points, require_file, run_edmft_helper, safe_prepare_dir


def resolve_klist_source(cfg) -> Path:
    case = cfg.case
    explicit = cfg.root_dir / "inputs" / f"{case}.klist_band"
    if explicit.exists():
        return explicit

    raw = cfg.get("band.klist_source")
    if not raw:
        raise WorkflowError(
            f"Band path is missing. Provide inputs/{case}.klist_band or set band.klist_source in config.toml."
        )
    raw = str(raw)
    if raw.startswith("@wien:"):
        root = cfg.get("environment.wienroot")
        if not root:
            raise WorkflowError("band.klist_source uses @wien: but environment.wienroot is unset")
        return Path(str(root)) / "SRC_templates" / raw.split(":", 1)[1]
    return Path(raw).expanduser().resolve()


def prepare_band(cfg, force: bool = False) -> Path:
    """Prepare an independent real-axis A(k,w) directory; do not run numerical jobs.

    Preparation uses only lightweight file work plus dmft_copy.py. The heavy
    Intel/MKL/MPI environment belongs to the later standalone band PBS job.
    """
    case = cfg.case
    source = cfg.dmft_dir
    source_indmfl = require_file(source / f"{case}.indmfl")
    require_file(source / "info.iterate")
    sig = require_file(source / "maxent" / "Sig.out")

    out = safe_prepare_dir(source / "band", force=force)

    # Start from the converged Matsubara DMFT snapshot, not from DOS/onreal.
    # This keeps DOS and band completely independent.
    run_edmft_helper(
        cfg,
        "dmft_copy.py",
        [str(source)],
        cwd=out,
        log=out / "dmft_copy.log",
    )
    potentials = _copy_wien_potentials(cfg, out)

    sig_target = out / "sig.inp"
    shutil.copy2(sig, sig_target)

    ksrc = resolve_klist_source(cfg)
    require_file(ksrc)
    ktarget = out / f"{case}.klist_band"
    shutil.copy2(ksrc, ktarget)

    indmfl = require_file(out / f"{case}.indmfl")
    backup = _prepare_real_axis_indmfl(
        indmfl,
        nomega=int(cfg.get("band.nomega", 200)),
        wmin=float(cfg.get("band.wmin", -6.0)),
        wmax=float(cfg.get("band.wmax", 6.0)),
    )
    expected = count_klist_points(ktarget)

    prepare_log = out / "prepare.log"
    prepare_log.write_text(
        "\n".join([
            f"source_dmft={source}",
            f"self_energy_source={sig}",
            f"self_energy_target={sig_target}",
            f"indmfl_source={source_indmfl}",
            f"klist_source={ksrc}",
            f"klist_target={ktarget}",
            f"kpoints={expected}",
            f"indmfl_backup={backup}",
            "matsubara_flag=0",
            f"nomega={int(cfg.get('band.nomega', 200))}",
            f"wmin={float(cfg.get('band.wmin', -6.0))}",
            f"wmax={float(cfg.get('band.wmax', 6.0))}",
        ]) + "\n",
        encoding="utf-8",
    )

    manifest = write_stage_manifest(
        out,
        "band",
        sources={
            "matsubara_indmfl": source_indmfl,
            "real_axis_self_energy": sig,
            "klist_band": ksrc,
        },
        prepared=[backup, indmfl, sig_target, ktarget, out / "indmfl.diff", prepare_log, *potentials],
    )

    print(f"Band directory prepared: {out}")
    print(f"Band path source: {ksrc}")
    print(f"Band path contains {expected} k points")
    print(f"Self-energy: {sig} -> {sig_target}")
    print("indmfl Matsubara flag: 1 -> 0")
    print(f"Review changes: {out / 'indmfl.diff'}")
    print(f"Provenance manifest: {manifest}")
    return out
