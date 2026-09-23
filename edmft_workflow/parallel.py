from __future__ import annotations

from pathlib import Path
import os
import shlex

from .utils import WorkflowError, require_file, run_stage


def pbs_hosts() -> list[str]:
    """Return one host entry per allocated PBS slot, preserving PBS_NODEFILE order."""
    nodefile = os.environ.get("PBS_NODEFILE")
    if not nodefile:
        return []
    path = Path(nodefile)
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def allocated_ranks() -> int:
    hosts = pbs_hosts()
    if hosts:
        return len(hosts)
    for key in ("PBS_NP", "NCPUS"):
        raw = os.environ.get(key)
        if raw and raw.isdigit():
            return max(1, int(raw))
    return 1


def in_batch_job() -> bool:
    return bool(os.environ.get("PBS_JOBID") or os.environ.get("EDMFT_WORKFLOW_BATCH"))


def stage_ranks(cfg, stage: str, *, upper_bound: int | None = None) -> int:
    """Resolve the number of MPI ranks for a stage.

    `parallel.<stage>_ranks = "allocation"` means use the PBS allocation.
    Outside PBS, `parallel.foreground_ranks` is used as the allocation surrogate.
    An integer requests that many ranks but never exceeds the real PBS allocation.
    """
    alloc = allocated_ranks()
    if alloc == 1 and not in_batch_job():
        alloc = int(cfg.get("parallel.foreground_ranks", 1))

    raw = cfg.get(f"parallel.{stage}_ranks", "allocation")
    if isinstance(raw, str) and raw.lower() == "allocation":
        ranks = alloc
    else:
        ranks = int(raw)
        if in_batch_job():
            ranks = min(ranks, alloc)

    if upper_bound is not None:
        ranks = min(ranks, max(1, int(upper_bound)))
    return max(1, ranks)


def mpi_launch_tokens(cfg, ranks: int) -> list[str]:
    launcher = str(cfg.get("parallel.mpi_launcher", "mpirun"))
    np_flag = str(cfg.get("parallel.mpi_np_flag", "-np"))
    extra = cfg.get("parallel.mpi_extra_args", [])
    if isinstance(extra, str):
        extra = shlex.split(extra)
    return [launcher, np_flag, str(int(ranks)), *[str(x) for x in extra]]


def mpi_prefix(cfg, ranks: int) -> str:
    return " ".join(shlex.quote(x) for x in mpi_launch_tokens(cfg, ranks))


def write_edmft_mpi_prefix(cfg, cwd: Path, stage: str) -> int:
    """Write the files that Haule's DmftEnvironment actually consumes.

    Upstream eDMFT reads `mpi_prefix.dat` and optionally `mpi_prefix.dat2` from
    the current working directory.  `x_dmft.py lapw1` uses MPI2, so writing both
    files avoids accidentally falling back to serial execution.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    ranks = stage_ranks(cfg, stage)
    text = mpi_prefix(cfg, ranks) + "\n"
    (cwd / "mpi_prefix.dat").write_text(text, encoding="utf-8")
    if bool(cfg.get("parallel.write_mpi_prefix2", True)):
        ranks2_raw = cfg.get(f"parallel.{stage}_ranks2", None)
        if ranks2_raw is None:
            text2 = text
        else:
            if isinstance(ranks2_raw, str) and ranks2_raw.lower() == "allocation":
                ranks2 = stage_ranks(cfg, stage)
            else:
                ranks2 = int(ranks2_raw)
                if in_batch_job():
                    ranks2 = min(ranks2, allocated_ranks())
            text2 = mpi_prefix(cfg, max(1, ranks2)) + "\n"
        (cwd / "mpi_prefix.dat2").write_text(text2, encoding="utf-8")
    print(f"MPI prefix ({stage}): {text.strip()}")
    return ranks


def write_wien_machines(cfg, cwd: Path, klist: Path, stage: str) -> Path:
    """Create `.machines` using Haule's own `createW2kmachinef.py` helper.

    Under PBS the real `PBS_NODEFILE` is used.  For explicit foreground
    debugging we synthesize a local hostfile with `parallel.foreground_ranks`
    entries.  The helper also caps k-point parallelism to the number of k points.
    """
    require_file(klist)
    root = cfg.get("environment.edmft_root")
    if not root:
        raise WorkflowError("environment.edmft_root is required for createW2kmachinef.py")
    helper = Path(str(root)) / "createW2kmachinef.py"
    require_file(helper)

    nodefile_raw = os.environ.get("PBS_NODEFILE")
    if nodefile_raw and Path(nodefile_raw).is_file():
        nodefile = Path(nodefile_raw)
    else:
        ranks = stage_ranks(cfg, stage)
        nodefile = cwd / ".edmft_local_hosts"
        nodefile.write_text("".join("localhost\n" for _ in range(ranks)), encoding="utf-8")

    py = str(cfg.get("environment.python", "python"))
    run_stage(
        cfg,
        [py, str(helper), str(klist), str(nodefile)],
        cwd=cwd,
        log=cwd / "createW2kmachinef.log",
    )
    path = cwd / ".machines"
    require_file(path)
    print(f"WIEN2k .machines created for {stage}: {path}")
    return path
