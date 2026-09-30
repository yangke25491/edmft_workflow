from __future__ import annotations

from pathlib import Path
import os
import re
import shlex
import subprocess

from .utils import WorkflowError, require_file, run_stage


def _scheduler_env(cfg, key: str, default: str) -> str:
    if cfg is None:
        return default
    return str(cfg.get(f"scheduler.{key}", default))


def _nodefile_path(cfg=None) -> Path | None:
    env_name = _scheduler_env(cfg, "nodefile_env", "PBS_NODEFILE")
    raw = os.environ.get(env_name)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def pbs_hosts(cfg=None) -> list[str]:
    """Return host entries from the scheduler nodefile, preserving order.

    The workflow deliberately does not attach a meaning to one line.  Whether
    a line is a CPU slot or only a host is defined by scheduler.slot_count_source
    in site.toml.
    """
    path = _nodefile_path(cfg)
    if path is None:
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _slot_count_from_env(cfg) -> int | None:
    names = cfg.get("scheduler.slot_count_envs", ["PBS_NP", "NCPUS"]) if cfg else ["PBS_NP", "NCPUS"]
    if isinstance(names, str):
        names = [names]
    for key in names:
        raw = os.environ.get(str(key))
        if raw and raw.strip().isdigit():
            return max(1, int(raw.strip()))
    return None


def _slot_command(cfg) -> str | None:
    if cfg is None:
        return None
    raw = cfg.get("scheduler.slot_count_command")
    return str(raw).strip() if raw else None


def _render_slot_command(cfg, template: str) -> str:
    job_env = _scheduler_env(cfg, "jobid_env", "PBS_JOBID")
    node_env = _scheduler_env(cfg, "nodefile_env", "PBS_NODEFILE")
    jobid = os.environ.get(job_env, "")
    nodefile = os.environ.get(node_env, "")
    return template.replace("{jobid}", shlex.quote(jobid)).replace("{nodefile}", shlex.quote(nodefile))


def _slot_count_from_command(cfg) -> int | None:
    template = _slot_command(cfg)
    if not template:
        return None
    try:
        cp = subprocess.run(
            ["bash", "-lc", _render_slot_command(cfg, template)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    match = re.search(r"\b(\d+)\b", (cp.stdout or "").strip())
    return max(1, int(match.group(1))) if match else None


def allocated_ranks(cfg=None) -> int:
    """Return scheduler-allocated CPU/MPI slots using site.toml semantics.

    Supported sources are intentionally generic rather than scheduler names:
      nodefile : line count of the configured nodefile
      env      : first integer from scheduler.slot_count_envs
      command  : run scheduler.slot_count_command, which must print an integer
      auto     : env -> command -> nodefile
    """
    source = str(cfg.get("scheduler.slot_count_source", "nodefile") if cfg else "nodefile").lower()
    hosts = pbs_hosts(cfg)

    if source == "nodefile":
        return max(1, len(hosts)) if hosts else (_slot_count_from_env(cfg) or 1)
    if source == "env":
        return _slot_count_from_env(cfg) or (max(1, len(hosts)) if hosts else 1)
    if source == "command":
        return _slot_count_from_command(cfg) or _slot_count_from_env(cfg) or (max(1, len(hosts)) if hosts else 1)
    if source == "auto":
        return _slot_count_from_env(cfg) or _slot_count_from_command(cfg) or (max(1, len(hosts)) if hosts else 1)
    raise WorkflowError(
        f"Unknown scheduler.slot_count_source={source!r}; use nodefile, env, command, or auto"
    )


def in_batch_job(cfg=None) -> bool:
    job_env = _scheduler_env(cfg, "jobid_env", "PBS_JOBID")
    return bool(os.environ.get(job_env) or os.environ.get("EDMFT_WORKFLOW_BATCH"))


def stage_ranks(cfg, stage: str, *, upper_bound: int | None = None) -> int:
    alloc = allocated_ranks(cfg)
    if alloc == 1 and not in_batch_job(cfg):
        alloc = int(cfg.get("parallel.foreground_ranks", 1))

    raw = cfg.get(f"parallel.{stage}_ranks", "allocation")
    if isinstance(raw, str) and raw.lower() == "allocation":
        ranks = alloc
    else:
        ranks = int(raw)
        if in_batch_job(cfg):
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
                if in_batch_job(cfg):
                    ranks2 = min(ranks2, allocated_ranks(cfg))
            text2 = mpi_prefix(cfg, max(1, ranks2)) + "\n"
        (cwd / "mpi_prefix.dat2").write_text(text2, encoding="utf-8")
    print(f"MPI prefix ({stage}): {text.strip()}")
    return ranks


def _write_single_node_compact_machines(cfg, cwd: Path, stage: str) -> Path:
    """Create the compact single-node WIEN2k `.machines` format."""
    hosts = pbs_hosts(cfg)
    if hosts:
        unique = list(dict.fromkeys(hosts))
        if len(unique) != 1:
            raise WorkflowError(
                "parallel.wien_machines_mode='single_node_compact' requires a single scheduler node; "
                f"allocation contains {len(unique)} hosts: {unique}"
            )
        host = unique[0]
        np = allocated_ranks(cfg)
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
    """Create `.machines` according to the site-configured WIEN2k policy."""
    require_file(klist)
    mode = str(cfg.get("parallel.wien_machines_mode", "haule")).lower()

    if mode in {"single_node_compact", "validated_single_node"}:
        return _write_single_node_compact_machines(cfg, cwd, stage)

    if mode != "haule":
        raise WorkflowError(f"Unknown parallel.wien_machines_mode={mode!r}")

    root = cfg.get("environment.edmft_root")
    if not root:
        raise WorkflowError("software.edmft_root is required for createW2kmachinef.py")
    helper = Path(str(root)) / "createW2kmachinef.py"
    require_file(helper)

    nodefile = _nodefile_path(cfg)
    if nodefile is None:
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
