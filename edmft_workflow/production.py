from __future__ import annotations

from pathlib import Path
import shlex

from .utils import WorkflowError, require_file, run_stage


def init_layout(cfg) -> None:
    """Create the fixed two-directory project layout without running init_lapw/init_dmft.py."""
    cfg.dft_dir.mkdir(parents=True, exist_ok=True)
    cfg.dmft_dir.mkdir(parents=True, exist_ok=True)
    cfg.scratch_dir.mkdir(parents=True, exist_ok=True)
    print(f"project root : {cfg.root_dir}")
    print(f"DFT dir      : {cfg.dft_dir}")
    print(f"DMFT dir     : {cfg.dmft_dir}")
    print(f"DFT SCRATCH  : {cfg.scratch_dir}")
    print("Next manual checkpoint: cd dft && init_lapw")


def run_dft(cfg) -> Path:
    """Run the ordinary WIEN2k DFT calculation after the user has run init_lapw manually."""
    case = cfg.case
    require_file(cfg.dft_dir / f"{case}.struct")
    require_file(cfg.dft_dir / f"{case}.in0")
    require_file(cfg.dft_dir / f"{case}.in1")
    cfg.scratch_dir.mkdir(parents=True, exist_ok=True)

    cmd = str(cfg.get("dft.run_command", "run_lapw -ec 0.0001 -cc 0.0001"))
    run_stage(
        cfg,
        cmd,
        cwd=cfg.dft_dir,
        scratch=cfg.scratch_dir,
        log=cfg.dft_dir / "edmft_workflow_dft.log",
    )
    require_file(cfg.dft_dir / f"{case}.scf")
    print(f"DFT run complete: {cfg.dft_dir}")
    print("Next: prepare the dmft/ directory, then run init_dmft.py there MANUALLY.")
    return cfg.dft_dir


def prepare_dmft(cfg, force: bool = False) -> Path:
    """Populate dmft/ from dft/ but deliberately do not run init_dmft.py."""
    case = cfg.case
    require_file(cfg.dft_dir / f"{case}.struct")
    require_file(cfg.dft_dir / f"{case}.scf")
    cfg.dmft_dir.mkdir(parents=True, exist_ok=True)

    # Protect an already initialized/running DMFT directory.
    important = [cfg.dmft_dir / f"{case}.indmfl", cfg.dmft_dir / "params.dat", cfg.dmft_dir / "info.iterate"]
    if any(p.exists() for p in important) and not force:
        raise WorkflowError(
            "dmft/ already contains DMFT initialization/run files. "
            "Refusing to overwrite them; use --force only if you intentionally want dmft_copy.py to refresh DFT files."
        )

    dmft_copy = str(cfg.get("commands.dmft_copy", "dmft_copy.py"))
    run_stage(cfg, [dmft_copy, str(cfg.dft_dir)], cwd=cfg.dmft_dir, log=cfg.dmft_dir / "dmft_copy_from_dft.log")
    require_file(cfg.dmft_dir / f"{case}.struct")
    print(f"DMFT working directory prepared: {cfg.dmft_dir}")
    print("MANUAL CHECKPOINT: cd dmft && init_dmft.py")
    return cfg.dmft_dir


def run_dmft(cfg) -> Path:
    """Run Haule's run_dmft.py after the user has completed init_dmft.py manually."""
    case = cfg.case
    require_file(cfg.dmft_dir / f"{case}.struct")
    require_file(cfg.dmft_dir / f"{case}.indmfl")
    require_file(cfg.dmft_dir / f"{case}.indmfi")
    require_file(cfg.dmft_dir / "params.dat")

    cmd = cfg.get("dmft.run_command")
    if not cmd:
        py = str(cfg.get("environment.python", "python"))
        root = cfg.get("environment.edmft_root")
        if not root:
            raise WorkflowError("environment.edmft_root is required to locate run_dmft.py")
        cmd = f"{shlex.quote(py)} {shlex.quote(str(Path(str(root)) / 'run_dmft.py'))}"

    # Current Haule eDMFT Python utils use '.' as the effective W2kEnvironment
    # SCRATCH for x_dmft/run_dmft. Therefore DMFT vector/band working files stay
    # local to dmft/, while the ordinary DFT stage uses dft/tmp.
    run_stage(
        cfg,
        str(cmd),
        cwd=cfg.dmft_dir,
        log=cfg.dmft_dir / "run_dmft.log",
    )
    require_file(cfg.dmft_dir / "info.iterate")
    print(f"DMFT run complete: {cfg.dmft_dir}")
    return cfg.dmft_dir
