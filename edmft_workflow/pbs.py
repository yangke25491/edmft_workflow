from __future__ import annotations

from pathlib import Path
import shlex
import time

from .provenance import refresh_stage_manifest
from .utils import WorkflowError, shell_preamble


PBS_STAGES = ("dft", "dmft", "maxent")


def _stage_resources(cfg, stage: str) -> dict:
    base = cfg.section("pbs")
    stage_specific = cfg.section(f"pbs_{stage}")
    out = dict(base)
    out.update(stage_specific)
    return out


def _stage_dir(cfg, stage: str) -> Path:
    if stage == "dft":
        return cfg.dft_dir
    if stage == "dmft":
        return cfg.dmft_dir
    if stage == "maxent":
        return cfg.dmft_dir / "maxent"
    raise WorkflowError(f"PBS is only used for: {', '.join(PBS_STAGES)}; got {stage}")


def _mpi_launcher(cfg) -> str:
    configured = cfg.get("parallel.mpi_launcher")
    return str(configured) if configured else "mpirun"


def _maxent_mpi_launcher(cfg) -> str:
    configured = cfg.get("maxent.mpi_launcher")
    return str(configured) if configured else "mpirun"


def _shell_slot_command(cfg, template: str) -> str:
    job_env = str(cfg.get("scheduler.jobid_env", "PBS_JOBID"))
    node_env = str(cfg.get("scheduler.nodefile_env", "PBS_NODEFILE"))
    return template.replace("{jobid}", f'"${{{job_env}:-}}"').replace(
        "{nodefile}", f'"${{{node_env}:-}}"'
    )


def _pbs_np_snippet(cfg) -> list[str]:
    """Freeze the site-defined scheduler slot-count policy into the PBS script."""
    source = str(cfg.get("scheduler.slot_count_source", "nodefile")).lower()
    node_env = str(cfg.get("scheduler.nodefile_env", "PBS_NODEFILE"))
    command = cfg.get("scheduler.slot_count_command")
    envs = cfg.get("scheduler.slot_count_envs", ["PBS_NP", "NCPUS"])
    if isinstance(envs, str):
        envs = [envs]

    lines: list[str] = ["NP="]
    if source in {"env", "auto"}:
        for name in envs:
            name = str(name)
            lines += [
                f'if [ -z "$NP" ] && [ -n "${{{name}:-}}" ]; then NP="${{{name}}}"; fi'
            ]
    if source in {"command", "auto"} and command:
        cmd = _shell_slot_command(cfg, str(command))
        lines += [f'if [ -z "$NP" ]; then NP=$({cmd}); fi']
    if source in {"nodefile", "auto"}:
        lines += [
            f'if [ -z "$NP" ] && [ -n "${{{node_env}:-}}" ]; then NP=$(wc -l < "${{{node_env}}}"); fi'
        ]
    if source not in {"nodefile", "env", "command", "auto"}:
        raise WorkflowError(
            f"Unknown scheduler.slot_count_source={source!r}; use nodefile, env, command, or auto"
        )
    lines += [
        'case "$NP" in',
        "    ''|*[!0-9]*) echo \"ERROR: site scheduler slot-count policy did not return an integer: '$NP'\" >&2; exit 2 ;;",
        "esac",
        'if [ "$NP" -lt 1 ]; then echo "ERROR: allocated slot count must be >=1" >&2; exit 2; fi',
    ]
    return lines


def _mpi_prefix_lines(cfg) -> list[str]:
    launcher = _mpi_launcher(cfg)
    npflag = str(cfg.get("parallel.mpi_np_flag", "-np"))
    extra = cfg.get("parallel.mpi_extra_args", [])
    if isinstance(extra, str):
        extra = shlex.split(extra)
    extra_text = " ".join(shlex.quote(str(x)) for x in extra)
    cmd = f"$MPI {shlex.quote(npflag)} $NP"
    if extra_text:
        cmd += " " + extra_text
    lines = [
        *_pbs_np_snippet(cfg),
        f"MPI={shlex.quote(launcher)}",
        f'echo "{cmd}" > mpi_prefix.dat',
    ]
    if bool(cfg.get("parallel.write_mpi_prefix2", True)):
        lines.append("cp mpi_prefix.dat mpi_prefix.dat2")
    lines += [
        'echo "MPI processes=$NP"',
        'echo "mpi_prefix.dat:"',
        "cat mpi_prefix.dat",
    ]
    return lines


def _native_commands(cfg, stage: str) -> list[str]:
    case = cfg.case
    wienroot = str(cfg.require("environment.wienroot"))
    edmft_root = str(cfg.require("environment.edmft_root"))
    python = str(cfg.get("environment.python", "python"))

    if stage == "dft":
        run = str(
            cfg.get(
                "dft.run_command",
                f"{shlex.quote(str(Path(wienroot) / 'run_lapw'))} -p -cc 0.0001 -ec 0.0001 -i 100",
            )
        )
        return [
            *_pbs_np_snippet(cfg),
            f'NODEFILE="${{{str(cfg.get("scheduler.nodefile_env", "PBS_NODEFILE"))}:-}}"',
            'if [ -z "$NODEFILE" ] || [ ! -s "$NODEFILE" ]; then echo "ERROR: scheduler nodefile is unavailable" >&2; exit 2; fi',
            'HOST=$(head -n 1 "$NODEFILE")',
            'printf "1:%s:%s\\n" "$HOST" "$NP" > .machines',
            'printf "%s\\n" "granularity:1" "extrafine:1" >> .machines',
            'echo ".machines:"',
            'cat .machines',
            f"{run} > wien2k_run.out 2>&1",
            f"test -s {shlex.quote(case + '.scf')}",
        ]

    if stage == "dmft":
        run = cfg.get("dmft.run_command")
        if run:
            command = str(run)
        else:
            command = f"{shlex.quote(python)} {shlex.quote(str(Path(edmft_root) / 'run_dmft.py'))}"
        return [
            *_mpi_prefix_lines(cfg),
            f"{command} > dmft.out 2>&1",
            "test -s info.iterate",
        ]

    if stage == "maxent":
        full_override = cfg.get("maxent.run_command")
        if full_override:
            return [
                *_pbs_np_snippet(cfg),
                f"{str(full_override)} > sig1.out 2>&1",
                "test -s Sig.out",
            ]
        launcher = _maxent_mpi_launcher(cfg)
        npflag = str(cfg.get("maxent.mpi_np_flag", "-np"))
        extra = cfg.get("maxent.mpi_extra_args", [])
        if isinstance(extra, str):
            extra = shlex.split(extra)
        extra_text = " ".join(shlex.quote(str(x)) for x in extra)
        maxent = shlex.quote(str(Path(edmft_root) / "maxent_run.py"))
        launch = f'"$MPI" {shlex.quote(npflag)} "$NP"'
        if extra_text:
            launch += " " + extra_text
        return [
            *_pbs_np_snippet(cfg),
            f"MPI={shlex.quote(launcher)}",
            f"echo \"$MPI {npflag} $NP{(' ' + extra_text) if extra_text else ''}\" > mpi_prefix.dat",
            'echo "MaxEnt MPI launcher=$MPI"',
            'echo "MaxEnt MPI ranks=$NP"',
            f"{launch} {shlex.quote(python)} {maxent} sig.inpx > sig1.out 2>&1",
            "test -s Sig.out",
        ]

    raise WorkflowError(f"Unsupported PBS stage: {stage}")


def render_pbs(cfg, stage: str) -> str:
    """Render a standalone PBS script from config.toml + the selected site profile."""
    if stage not in PBS_STAGES:
        raise WorkflowError(f"PBS is only used for: {', '.join(PBS_STAGES)}")

    r = _stage_resources(cfg, stage)
    name = str(r.get("job_name", f"{cfg.case}_{stage}"))
    nodes = int(r.get("nodes", 1))
    ppn = int(r.get("ppn", 8))
    walltime = str(r.get("walltime", "04:00:00"))
    mem = str(r.get("mem", "")).strip()
    queue = r.get("queue")
    cwd = _stage_dir(cfg, stage)
    scratch = cwd / "tmp"

    resource_template = str(
        cfg.get("scheduler.resource_template", "#PBS -l nodes={nodes}:ppn={ppn}")
    )
    resource_line = resource_template.format(nodes=nodes, ppn=ppn, mem=mem, walltime=walltime, queue=queue or "")

    lines = [
        "#!/bin/bash",
        f"#PBS -N {name}",
        "#PBS -j oe",
        resource_line,
        f"#PBS -l walltime={walltime}",
    ]
    if mem:
        mem_template = str(cfg.get("scheduler.memory_template", "#PBS -l mem={mem}"))
        lines.append(mem_template.format(mem=mem, nodes=nodes, ppn=ppn))
    if queue:
        queue_template = str(cfg.get("scheduler.queue_template", "#PBS -q {queue}"))
        lines.append(queue_template.format(queue=queue))

    submit = str(cfg.get("scheduler.submit_command", "qsub"))
    job_env = str(cfg.get("scheduler.jobid_env", "PBS_JOBID"))
    node_env = str(cfg.get("scheduler.nodefile_env", "PBS_NODEFILE"))

    lines += [
        "",
        "# Generated by edmft_workflow. This file is intentionally standalone.",
        "# Runtime/site settings are frozen here; compute nodes do not read config.toml or site.toml.",
        f"# Inspect it, then submit manually with: {submit} <this-file>",
        "",
    ]

    preamble = shell_preamble(
        cfg,
        cwd,
        scratch=scratch,
        profile="maxent" if stage == "maxent" else "native",
    )

    lines += [
        preamble,
        "",
        f'echo "scheduler job id=${{{job_env}:-none}}"',
        f'echo "scheduler nodefile=${{{node_env}:-none}}"',
        'echo "working directory=$PWD"',
        "",
        *_native_commands(cfg, stage),
        "",
        f'echo "{stage} native commands completed and required output exists"',
    ]
    return "\n".join(lines) + "\n"


def write_pbs(cfg, stage: str, force: bool = False) -> Path:
    out = _stage_dir(cfg, stage)
    if not out.exists():
        raise WorkflowError(f"Stage directory does not exist: {out}. Prepare the stage first.")
    path = out / f"run_{stage}.pbs"
    if path.exists():
        if not force:
            raise WorkflowError(f"PBS script already exists: {path}. Use --force to back it up and regenerate it.")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(path.name + f".bak.{stamp}")
        path.rename(backup)
        print(f"[backup] {path} -> {backup}")
    path.write_text(render_pbs(cfg, stage), encoding="utf-8")
    manifest = refresh_stage_manifest(out, extra_prepared=[path])
    submit = str(cfg.get("scheduler.submit_command", "qsub"))
    print(f"Standalone PBS written: {path}")
    if manifest is not None:
        print(f"Manifest refreshed at PBS freeze point: {manifest}")
    print(f"Inspect: cat {path}")
    print(f"Submit manually: {submit} {path}")
    return path
