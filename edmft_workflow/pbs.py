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
            "environment.python must point to .../envs/NAME/bin/python so MaxEnt can infer the matching conda MPI runtime"
        )
    return python.parent.parent


def _maxent_mpi_launcher(cfg) -> str:
    """MPI launcher for MaxEnt/mpi4py, separate from native Intel MPI.

    The configured Python environment may have mpi4py built against Open MPI
    while WIEN2k/eDMFT native binaries use Intel MPI. Reusing Intel mpirun in
    that case produces singleton ranks and the mpi4py 'suspicious MPI execution
    environment' warning. By default we therefore use mpirun from the same
    environment as the configured Python interpreter.
    """
    configured = cfg.get("maxent.mpi_launcher")
    if configured:
        return str(configured)
    return str(_python_env_root(cfg) / "bin" / "mpirun")


def _maxent_preamble(cfg, cwd: Path, scratch: Path) -> str:
    """Render an isolated Open-MPI/mpi4py runtime for parallel MaxEnt.

    Intel MPI paths are intentionally excluded. Intel MKL/compiler runtime
    libraries remain available because compiled Python/eDMFT extensions may
    need them. This keeps MaxEnt's MPI vendor consistent with mpi4py without
    changing the user's login shell.
    """
    env_root = _python_env_root(cfg).resolve()
    python = Path(str(cfg.require("environment.python"))).expanduser().resolve()
    intel = Path(str(cfg.require("environment.intel_root"))).expanduser()
    wienroot = Path(str(cfg.require("environment.wienroot"))).expanduser()
    edmft_root = Path(str(cfg.require("environment.edmft_root"))).expanduser()
    fftw = Path(str(cfg.get("environment.fftw_lib", "/opt/fftw-3.3.10/lib"))).expanduser()

    conda_root = env_root.parent.parent if env_root.parent.name == "envs" else env_root.parent
    conda_sh = conda_root / "etc/profile.d/conda.sh"

    ld_library_path = ":".join(
        [
            str(env_root / "lib"),
            str(intel / "linux/mkl/lib/intel64"),
            str(intel / "linux/compiler/lib/intel64_lin"),
            str(fftw),
        ]
    )

    lines = [
        "set -e",
        f"cd {shlex.quote(str(cwd))}",
        f"ENV={shlex.quote(str(env_root))}",
        f"PYTHON={shlex.quote(str(python))}",
        f"WIENROOT={shlex.quote(str(wienroot))}",
        f"WIEN_DMFT_ROOT={shlex.quote(str(edmft_root))}",
        f"CONDA_SH={shlex.quote(str(conda_sh))}",
        'if [ -f "$CONDA_SH" ]; then',
        '  source "$CONDA_SH"',
        '  conda activate "$ENV"',
        "fi",
        'export WIENROOT WIEN_DMFT_ROOT',
        'export PATH="$ENV/bin:$WIEN_DMFT_ROOT:$WIENROOT:/usr/bin:/bin"',
        'export PYTHONPATH="$WIEN_DMFT_ROOT${PYTHONPATH:+:$PYTHONPATH}"',
        f"export LD_LIBRARY_PATH={shlex.quote(ld_library_path)}",
        "unset I_MPI_HYDRA_BOOTSTRAP I_MPI_FABRICS",
        "export OMP_NUM_THREADS=1",
        "export MKL_NUM_THREADS=1",
        f"export SCRATCH={shlex.quote(str(scratch))}",
        'mkdir -p "$SCRATCH"',
        "ulimit -s unlimited",
        "ulimit -c unlimited",
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
        launcher = _maxent_mpi_launcher(cfg)
        npflag = str(cfg.get("maxent.mpi_np_flag", "-np"))
        maxent = shlex.quote(str(Path(edmft_root) / "maxent_run.py"))
        return [
            'NP=$(wc -l < "$PBS_NODEFILE")',
            f"MAXENT_MPI={shlex.quote(launcher)}",
            'echo "MaxEnt MPI ranks=$NP"',
            'echo "MaxEnt MPI launcher=$MAXENT_MPI"',
            '"$PYTHON" -c "from mpi4py import MPI; print(\"mpi4py library:\", MPI.Get_library_version().splitlines()[0])"',
            '"$MAXENT_MPI" --version | head -n 1',
            'awk \'{n[$1]++} END {for (h in n) print h " slots=" n[h]}\' "$PBS_NODEFILE" > maxent.hosts',
            'echo "MaxEnt hostfile:"',
            'cat maxent.hosts',
            f'"$MAXENT_MPI" --hostfile maxent.hosts {shlex.quote(npflag)} "$NP" "$PYTHON" {maxent} Sig.average > sig1.out 2>&1',
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
        "",
    ]

    preamble = _maxent_preamble(cfg, cwd, scratch) if stage == "maxent" else shell_preamble(cfg, cwd, scratch=scratch)

    lines += [
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
