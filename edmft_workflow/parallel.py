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


def _write_single_node_compact_machines(cfg, cwd: Path, stage: str) -> Path:
    """Reproduce the user's validated single-node WIEN2k `.machines` file.

    PBS form:
        1:<first-host>:<number-of-slots>
        granularity:1
        extrafine:1
    """
    hosts = pbs_hosts()
    if hosts:
        unique = list(dict.fromkeys(hosts))
        if len(unique) != 1:
            raise WorkflowError(
                "parallel.wien_machines_mode='single_node_compact' requires a single PBS node; "
                f"allocation contains {len(unique)} hosts: {unique}"
            )
        host = unique[0]
        np = len(hosts)
    else:
        host = "localhost"
        np = stage_ranks(cfg, stage)

    path = cwd / ".machines"
    path.write_text(
        f"1:{host}:{np}\n"
        "granularity:1\n"
        "extrafine:1\n",
        encoding="utf-8",
    )
    print(f"WIEN2k .machines ({stage}, single_node_compact):\n{path.read_text()}")
    return path


def write_wien_machines(cfg, cwd: Path, klist: Path, stage: str) -> Path:
    """Create `.machines` according to the configured WIEN2k parallel policy."""
    require_file(klist)
    mode = str(cfg.get("parallel.wien_machines_mode", "haule")).lower()

    if mode in {"single_node_compact", "validated_single_node"}:
        return _write_single_node_compact_machines(cfg, cwd, stage)

    if mode != "haule":
        raise WorkflowError(f"Unknown parallel.wien_machines_mode={mode!r}")

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
