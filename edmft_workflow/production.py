from __future__ import annotations

from pathlib import Path
import shlex

from .parallel import write_edmft_mpi_prefix, write_wien_machines
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
    print("Next manual checkpoint: cd dft && export SCRATCH=$PWD/tmp && init_lapw")


def run_dft(cfg) -> Path:
    """Run the ordinary WIEN2k DFT calculation after manual `init_lapw`."""
    case = cfg.case
    require_file(cfg.dft_dir / f"{case}.struct")
    require_file(cfg.dft_dir / f"{case}.in0")
    require_file(cfg.dft_dir / f"{case}.in1")
    klist = require_file(cfg.dft_dir / f"{case}.klist")
    cfg.scratch_dir.mkdir(parents=True, exist_ok=True)

    # WIEN2k does not obtain k-point parallelism merely from PBS/mpirun.
    # `run_lapw -p` needs a `.machines` file.  Use Haule's helper so its
    # distribution logic stays consistent with eDMFT.
    write_wien_machines(cfg, cfg.dft_dir, klist, "dft")

    cmd = str(cfg.get("dft.run_command", "run_lapw -p -ec 0.0001 -cc 0.0001"))
    if " -p" not in f" {cmd} ":
        cmd += " -p"
    run_stage(
        cfg,
        cmd,
        cwd=cfg.dft_dir,
        scratch=cfg.scratch_dir,
        log=cfg.dft_dir / "edmft_workflow_dft.log",
    )
    require_file(cfg.dft_dir / f"{case}.scf")
    print(f"DFT run complete: {cfg.dft_dir}")
    print("Next: prepare dmft/, then run init_dmft.py there MANUALLY.")
    return cfg.dft_dir


def prepare_dmft(cfg, force: bool = False) -> Path:
    """Populate dmft/ from dft/ but deliberately do not run init_dmft.py.

    Important `dmft_copy.py` semantics: its positional argument is the SOURCE
    directory, while the DESTINATION is always the current working directory.
    Consequently we execute it with cwd=dmft/ and pass dft/ as the sole source.
    """
    case = cfg.case
    require_file(cfg.dft_dir / f"{case}.struct")
    require_file(cfg.dft_dir / f"{case}.scf")
    cfg.dmft_dir.mkdir(parents=True, exist_ok=True)

    important = [
        cfg.dmft_dir / f"{case}.indmfl",
        cfg.dmft_dir / "params.dat",
        cfg.dmft_dir / "info.iterate",
    ]
    if any(p.exists() for p in important) and not force:
        raise WorkflowError(
            "dmft/ already contains DMFT initialization/run files. Refusing to overwrite them; "
            "use --force only if you intentionally want dmft_copy.py to refresh DFT files."
        )

    dmft_copy = str(cfg.get("commands.dmft_copy", "dmft_copy.py"))
    run_stage(
        cfg,
        [dmft_copy, str(cfg.dft_dir)],
        cwd=cfg.dmft_dir,
        log=cfg.dmft_dir / "dmft_copy_from_dft.log",
    )
    require_file(cfg.dmft_dir / f"{case}.struct")
    print(f"DMFT working directory prepared: {cfg.dmft_dir}")
    print("MANUAL CHECKPOINT: cd dmft && init_dmft.py")
    return cfg.dmft_dir


def run_dmft(cfg) -> Path:
    """Run Haule's charge-self-consistent DFT+DMFT after manual `init_dmft.py`."""
    case = cfg.case
    require_file(cfg.dmft_dir / f"{case}.struct")
    require_file(cfg.dmft_dir / f"{case}.indmfl")
    require_file(cfg.dmft_dir / f"{case}.indmfi")
    require_file(cfg.dmft_dir / "params.dat")
    klist = require_file(cfg.dmft_dir / f"{case}.klist")

    # Two independent parallel mechanisms are used by upstream run_dmft.py:
    #   1) mpi_prefix.dat(.2) for eDMFT executables / internal LAPW1;
    #   2) .machines for WIEN2k parallel sections.
    # Upstream run_dmft.py explicitly switches to WIEN2k -p when .machines exists.
    write_edmft_mpi_prefix(cfg, cfg.dmft_dir, "dmft")
    if bool(cfg.get("parallel.dmft_wien_machines", True)):
        write_wien_machines(cfg, cfg.dmft_dir, klist, "dmft")

    cmd = cfg.get("dmft.run_command")
    if not cmd:
        py = str(cfg.get("environment.python", "python"))
        root = cfg.get("environment.edmft_root")
        if not root:
            raise WorkflowError("environment.edmft_root is required to locate run_dmft.py")
        cmd = f"{shlex.quote(py)} {shlex.quote(str(Path(str(root)) / 'run_dmft.py'))}"

    run_stage(
        cfg,
        str(cmd),
        cwd=cfg.dmft_dir,
        log=cfg.dmft_dir / "run_dmft.log",
    )
    require_file(cfg.dmft_dir / "info.iterate")
    print(f"DMFT run complete: {cfg.dmft_dir}")
    return cfg.dmft_dir
