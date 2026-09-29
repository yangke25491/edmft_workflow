from __future__ import annotations

from pathlib import Path
import shlex

from .utils import WorkflowError, require_file, run, shell_preamble


FOREGROUND_STAGES = ("dos", "band")


def _stage_dir(cfg, stage: str) -> Path:
    if stage == "dos":
        return cfg.dmft_dir / "onreal"
    if stage == "band":
        return cfg.dmft_dir / "band"
    raise WorkflowError(f"Foreground MPI is only used for: {', '.join(FOREGROUND_STAGES)}")


def _mpi_launcher(cfg) -> str:
    configured = cfg.get("parallel.mpi_launcher")
    if configured:
        return str(configured)
    intel = cfg.get("environment.intel_root")
    if intel:
        return str(Path(str(intel)) / "linux/mpi/intel64/bin/mpirun")
    return "mpirun"


def _stage_np(cfg, stage: str) -> int:
    value = cfg.get(f"foreground.{stage}_np", cfg.get("foreground.np", 8))
    np = int(value)
    if np < 1:
        raise WorkflowError(f"foreground.{stage}_np must be >= 1, got {np}")
    return np


def render_foreground(cfg, stage: str) -> str:
    """Render the child-shell script used for local/foreground MPI postprocessing."""
    if stage not in FOREGROUND_STAGES:
        raise WorkflowError(f"Foreground MPI is only used for: {', '.join(FOREGROUND_STAGES)}")

    case = cfg.case
    cwd = _stage_dir(cfg, stage)
    scratch = cwd / "tmp"
    np = _stage_np(cfg, stage)
    launcher = _mpi_launcher(cfg)
    npflag = str(cfg.get("parallel.mpi_np_flag", "-np"))
    wienroot = str(cfg.require("environment.wienroot"))
    edmft_root = str(cfg.require("environment.edmft_root"))

    lines = [
        shell_preamble(cfg, cwd, scratch=scratch),
        "set -o pipefail",
        f"NP={np}",
        f"MPI={shlex.quote(launcher)}",
        f"echo \"$MPI {npflag} $NP\" > mpi_prefix.dat",
    ]
    if bool(cfg.get("parallel.write_mpi_prefix2", True)):
        lines.append("cp mpi_prefix.dat mpi_prefix.dat2")
    lines += [
        'echo "foreground MPI ranks=$NP"',
        'echo "mpi_prefix.dat:"',
        "cat mpi_prefix.dat",
        "",
    ]

    if stage == "dos":
        lines += [
            f"{shlex.quote(str(Path(wienroot) / 'x_lapw'))} -f {shlex.quote(case)} lapw0 2>&1 | tee lapw0.log",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} lapw1 2>&1 | tee lapw1.log",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} dmft1 2>&1 | tee dmft1.log",
            f"test -s {shlex.quote(case + '.cdos')}",
        ]
    else:
        lines += [
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} lapw1 --band 2>&1 | tee lapw1_band.log",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} dmftp 2>&1 | tee dmftp.log",
            "test -s eigvals.dat",
        ]

    return "\n".join(lines) + "\n"


def run_foreground(cfg, stage: str) -> Path:
    """Run DOS or band postprocessing in a disposable foreground child shell."""
    cwd = _stage_dir(cfg, stage)
    if not cwd.exists():
        raise WorkflowError(f"Stage directory does not exist: {cwd}. Run prepare-{stage} first.")

    if stage == "dos":
        require_file(cwd / f"{cfg.case}.indmfl")
        require_file(cwd / "sig.inp")
        output = cwd / f"{cfg.case}.cdos"
    else:
        require_file(cwd / f"{cfg.case}.indmfl")
        require_file(cwd / f"{cfg.case}.klist_band")
        require_file(cwd / "sig.inp")
        output = cwd / "eigvals.dat"

    script = render_foreground(cfg, stage)
    print(f"[foreground-{stage}] working directory: {cwd}")
    print(f"[foreground-{stage}] MPI ranks: {_stage_np(cfg, stage)}")
    run(["bash", "-lc", script], cwd=cwd)
    require_file(output)
    print(f"Foreground {stage} completed: {output}")
    return output
