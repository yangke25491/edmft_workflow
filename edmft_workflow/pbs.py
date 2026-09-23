from __future__ import annotations

from pathlib import Path
import re
import shlex
import subprocess

from .utils import WorkflowError, shell_preamble


def _stage_resources(cfg, stage: str) -> dict:
    base = cfg.section("pbs")
    stage_specific = cfg.section(f"pbs_{stage}")
    out = dict(base)
    out.update(stage_specific)
    return out


def render_pbs(cfg, stage: str, force: bool = False) -> str:
    r = _stage_resources(cfg, stage)
    name = str(r.get("job_name", f"{cfg.case}_{stage}"))
    nodes = int(r.get("nodes", 1))
    ppn = int(r.get("ppn", 8))
    walltime = str(r.get("walltime", "04:00:00"))
    mem = str(r.get("mem", "16gb"))
    queue = r.get("queue")
    py = str(cfg.get("environment.python", "python"))
    repo_root = Path(__file__).resolve().parent.parent
    config_path = cfg.source.resolve()
    cmd = (
        f"{shlex.quote(py)} -m edmft_workflow.cli "
        f"-c {shlex.quote(str(config_path))} run {stage}"
    )
    if force:
        cmd += " --force"

    lines = [
        "#!/bin/bash",
        f"#PBS -N {name}",
        f"#PBS -l nodes={nodes}:ppn={ppn}",
        f"#PBS -l walltime={walltime}",
        f"#PBS -l mem={mem}",
        "#PBS -j oe",
    ]
    if queue:
        lines.append(f"#PBS -q {queue}")
    lines += [
        "",
        shell_preamble(cfg, cfg.dmft_dir),
        f"export PYTHONPATH={shlex.quote(str(repo_root))}:${{PYTHONPATH:-}}",
        cmd,
        "",
    ]
    return "\n".join(lines)


def write_pbs(cfg, stage: str, force: bool = False) -> Path:
    jobdir = cfg.work_root / ".edmft_jobs"
    jobdir.mkdir(parents=True, exist_ok=True)
    path = jobdir / f"{stage}.pbs"
    path.write_text(render_pbs(cfg, stage, force=force), encoding="utf-8")
    return path


def submit_pbs(cfg, stage: str, force: bool = False, depends_on: str | None = None) -> str:
    script = write_pbs(cfg, stage, force=force)
    qsub = str(cfg.get("pbs.qsub", "qsub"))
    cmd = [qsub]
    if depends_on:
        cmd += ["-W", f"depend=afterok:{depends_on}"]
    cmd.append(str(script))
    cp = subprocess.run(cmd, text=True, capture_output=True)
    if cp.returncode != 0:
        raise WorkflowError(f"qsub failed for {stage}: {cp.stderr.strip()}")
    text = cp.stdout.strip()
    m = re.search(r"([0-9]+(?:\.[A-Za-z0-9_.-]+)?)", text)
    jobid = m.group(1) if m else text
    print(f"submitted {stage}: {jobid}")
    return jobid
