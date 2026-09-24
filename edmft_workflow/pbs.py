from __future__ import annotations

from pathlib import Path
import shlex
import time

from .provenance import refresh_stage_manifest
from .utils import WorkflowError, shell_preamble


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
    if stage == "dos":
        return cfg.dmft_dir / "onreal"
    if stage == "band":
        return cfg.dmft_dir / "band"
    raise WorkflowError(f"Unsupported PBS stage: {stage}")


def _mpi_launcher(cfg) -> str:
    configured = cfg.get("parallel.mpi_launcher")
    if configured:
        return str(configured)
    intel = cfg.get("environment.intel_root")
    if intel:
        return str(Path(str(intel)) / "linux/mpi/intel64/bin/mpirun")
    return "mpirun"


def _python_env_root(cfg) -> Path:
    python = Path(str(cfg.require("environment.python"))).expanduser()
    if python.parent.name != "bin":
        raise WorkflowError(
            "environment.python must point to .../envs/NAME/bin/python so the MaxEnt PBS can activate the same conda environment"
        )
    return python.parent.parent


def _maxent_preamble(cfg, cwd: Path, scratch: Path) -> str:
    """Render the validated MaxEnt runtime used on the target cluster.

    Important: ``maxent_run.py`` is launched directly with Python. We still
    write ``mpi_prefix.dat`` for consistency with the eDMFT working directory,
    but upstream maxent_run.py does not consume that file. Direct Python launch
    therefore runs with mpi4py COMM_WORLD size=1 unless the command itself is
    launched through a compatible MPI implementation.
    """
    intel = Path(str(cfg.require("environment.intel_root"))).expanduser()
    env_root = _python_env_root(cfg)
    env_name = env_root.name
    wienroot = str(cfg.require("environment.wienroot"))
    edmft_root = str(cfg.require("environment.edmft_root"))
    fftw = str(cfg.get("environment.fftw_lib", "/opt/fftw-3.3.10/lib"))
    arch = str(cfg.get("environment.intel_arch", "intel64"))
    conda_sh = env_root.parent.parent / "etc/profile.d/conda.sh"

    lines = [
        "set -e",
        f"cd {shlex.quote(str(cwd))}",
        f"INTEL={shlex.quote(str(intel))}",
        f"ENV={shlex.quote(str(env_root))}",
        f"source \"$INTEL/linux/bin/compilervars.sh\" {shlex.quote(arch)}",
        f"source {shlex.quote(str(conda_sh))}",
        f"conda activate {shlex.quote(env_name)}",
        f"export WIENROOT={shlex.quote(wienroot)}",
        f"export WIEN_DMFT_ROOT={shlex.quote(edmft_root)}",
        'export PYTHONPATH="$WIEN_DMFT_ROOT${PYTHONPATH:+:$PYTHONPATH}"',
        'export LD_LIBRARY_PATH="$INTEL/linux/mkl/lib/intel64:$INTEL/linux/compiler/lib/intel64_lin:$INTEL/linux/mpi/intel64/lib/release:$INTEL/linux/mpi/intel64/lib:'
        + shlex.quote(fftw)
        + ':$ENV/lib:${LD_LIBRARY_PATH:-}"',
        "export I_MPI_HYDRA_BOOTSTRAP=ssh",
        "export I_MPI_FABRICS=shm",
        "export OMP_NUM_THREADS=1",
        "export MKL_NUM_THREADS=1",
        f"export SCRATCH={shlex.quote(str(scratch))}",
        'mkdir -p "$SCRATCH"',
    ]
    return "\n".join(lines)


def _mpi_prefix_lines(cfg) -> list[str]:
    launcher = _mpi_launcher(cfg)
    npflag = str(cfg.get("parallel.mpi_np_flag", "-np"))
    lines = [
        'NP=$(wc -l < "$PBS_NODEFILE")',
        f"MPI={shlex.quote(launcher)}",
        f"echo \"$MPI {npflag} $NP\" > mpi_prefix.dat",
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
        cmd = cfg.get("dft.run_command")
        if cmd:
            run = str(cmd)
        else:
            run = f"{shlex.quote(str(Path(wienroot) / 'run_lapw'))} -p -cc 0.0001 -ec 0.0001 -i 100"
        return [
            'NP=$(wc -l < "$PBS_NODEFILE")',
            'HOST=$(head -n 1 "$PBS_NODEFILE")',
            'printf "1:%s:%s\\n" "$HOST" "$NP" > .machines',
            'printf "%s\\n" "granularity:1" "extrafine:1" >> .machines',
            'echo ".machines:"',
            'cat .machines',
            f"{run} > wien2k_run.out 2>&1",
            f"test -s {shlex.quote(case + '.scf')}",
        ]

    if stage == "dmft":
        return [
            *_mpi_prefix_lines(cfg),
            f"{shlex.quote(python)} {shlex.quote(str(Path(edmft_root) / 'run_dmft.py'))} > dmft.out 2>&1",
            "test -s info.iterate",
        ]

    if stage == "maxent":
        launcher = _mpi_launcher(cfg)
        npflag = str(cfg.get("parallel.mpi_np_flag", "-np"))
        return [
            'NP=$(wc -l < "$PBS_NODEFILE")',
            f"echo \"{shlex.quote(launcher)} {npflag} $NP\" > mpi_prefix.dat",
            'echo "mpi_prefix.dat:"',
            "cat mpi_prefix.dat",
            'echo "MaxEnt launch mode: direct Python (validated cluster workflow; mpi4py size=1)"',
            f"python {shlex.quote(str(Path(edmft_root) / 'maxent_run.py'))} Sig.average > sig1.out 2>&1",
            "test -s Sig.out",
        ]

    if stage == "dos":
        return [
            *_mpi_prefix_lines(cfg),
            f"{shlex.quote(str(Path(wienroot) / 'x_lapw'))} -f {shlex.quote(case)} lapw0 > lapw0.log 2>&1",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} lapw1 > lapw1.log 2>&1",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} dmft1 > dmft1.log 2>&1",
            f"test -s {shlex.quote(case + '.cdos')}",
        ]

    if stage == "band":
        return [
            *_mpi_prefix_lines(cfg),
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} lapw1 --band > lapw1_band.log 2>&1",
            f"{shlex.quote(str(Path(edmft_root) / 'x_dmft.py'))} dmftp > dmftp.log 2>&1",
            "test -s eigvals.dat",
        ]

    raise WorkflowError(f"Unsupported PBS stage: {stage}")


def render_pbs(cfg, stage: str) -> str:
    """Render a self-contained PBS script with visible native commands."""
    r = _stage_resources(cfg, stage)
    name = str(r.get("job_name", f"{cfg.case}_{stage}"))
    nodes = int(r.get("nodes", 1))
    ppn = int(r.get("ppn", 8))
    walltime = str(r.get("walltime", "04:00:00"))
    mem = str(r.get("mem", "")).strip()
    queue = r.get("queue")
    cwd = _stage_dir(cfg, stage)
    scratch = cwd / "tmp"

    lines = [
        "#!/bin/bash",
        f"#PBS -N {name}",
        "#PBS -j oe",
        f"#PBS -l nodes={nodes}:ppn={ppn}",
        f"#PBS -l walltime={walltime}",
    ]
    if mem:
        lines.append(f"#PBS -l mem={mem}")
    if queue:
        lines.append(f"#PBS -q {queue}")

    lines += [
        "",
        "# Generated by edmft_workflow. This file is intentionally standalone.",
        "# It contains no workflow runtime dependency or project-config lookup.",
        "# Inspect it, then submit manually with: qsub <this-file>",
    ]
    if stage == "maxent" and ppn != 1:
        lines += [
            "# NOTE: this validated MaxEnt launch is direct Python and therefore uses mpi4py size=1.",
            f"# pbs_maxent.ppn={ppn} reserves multiple slots but does not parallelize maxent_run.py.",
            "# Keep this while validating the workflow; parallel MaxEnt requires a launcher matching mpi4py's MPI vendor.",
        ]

    preamble = _maxent_preamble(cfg, cwd, scratch) if stage == "maxent" else shell_preamble(cfg, cwd, scratch=scratch)

    lines += [
        "",
        preamble,
        "",
        'echo "PBS_JOBID=${PBS_JOBID:-none}"',
        'echo "PBS_NODEFILE=${PBS_NODEFILE:-none}"',
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
    print(f"Standalone PBS written: {path}")
    if manifest is not None:
        print(f"Manifest refreshed at PBS freeze point: {manifest}")
    print(f"Inspect: cat {path}")
    print(f"Submit manually: qsub {path}")
    return path
