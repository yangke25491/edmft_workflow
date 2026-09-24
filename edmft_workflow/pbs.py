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
    """MPI launcher used by WIEN2k/eDMFT numerical stages.

    This is intentionally NOT reused for MaxEnt. The Python interpreter used by
    maxent_run.py may have mpi4py built against a different MPI implementation
    (for example Open MPI while the eDMFT binaries use Intel MPI). MaxEnt has a
    separate explicit launcher setting below.
    """
    configured = cfg.get("parallel.mpi_launcher")
    if configured:
        return str(configured)
    intel = cfg.get("environment.intel_root")
    if intel:
        return str(Path(str(intel)) / "linux/mpi/intel64/bin/mpirun")
    return "mpirun"


def _maxent_mpi_launcher(cfg) -> str | None:
    """Return an explicitly configured launcher for mpi4py MaxEnt, if any.

    Upstream to_real_axis.py invokes ``maxent_run.py sig.inpx`` directly. That
    is the safe default here as well. Parallel MaxEnt is opt-in because the MPI
    launcher must match the MPI vendor against which mpi4py was built.
    """
    configured = cfg.get("maxent.mpi_launcher")
    if configured is None:
        return None
    text = str(configured).strip()
    return text or None


def _maxent_preamble(cfg, cwd: Path, scratch: Path) -> str:
    """Render a Python/MaxEnt runtime without injecting Intel MPI.

    MaxEnt is a Python/mpi4py stage. The user's mpi4py can be built against Open
    MPI even when WIEN2k/eDMFT binaries use Intel MPI. Therefore this preamble
    deliberately omits Intel MPI bin/lib paths and I_MPI_* variables. It keeps
    the compiler/MKL runtime libraries that compiled MaxEnt extensions may need,
    plus the configured Python environment library directory.
    """
    lines = ["set -e", f"cd {shlex.quote(str(cwd))}"]

    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    pybin = cfg.get("environment.python_bin_dir")
    python = cfg.get("environment.python")
    intel_root = cfg.get("environment.intel_root")
    fftw_lib = cfg.get("environment.fftw_lib")

    if wienroot:
        lines.append(f"export WIENROOT={shlex.quote(str(wienroot))}")
    if edmft_root:
        lines.append(f"export WIEN_DMFT_ROOT={shlex.quote(str(edmft_root))}")

    path_parts = [x for x in (edmft_root, wienroot, pybin, "/usr/bin", "/bin") if x]
    if path_parts:
        lines.append("export PATH=" + shlex.quote(":".join(str(x) for x in path_parts)))

    ld_parts: list[str] = []
    if intel_root:
        intel = Path(str(intel_root)).expanduser()
        ld_parts += [
            str(intel / "linux/mkl/lib/intel64"),
            str(intel / "linux/compiler/lib/intel64_lin"),
        ]
    if fftw_lib:
        ld_parts.append(str(fftw_lib))
    if python:
        p = Path(str(python)).expanduser()
        if p.parent.name == "bin":
            ld_parts.append(str(p.parent.parent / "lib"))
    if ld_parts:
        lines.append(
            "export LD_LIBRARY_PATH="
            + shlex.quote(":".join(ld_parts))
            + ":${LD_LIBRARY_PATH:-}"
        )

    if edmft_root:
        lines.append(
            f"export PYTHONPATH={shlex.quote(str(edmft_root))}${{PYTHONPATH:+:${{PYTHONPATH}}}}"
        )

    stack = cfg.get("environment.ulimit_stack", "unlimited")
    core = cfg.get("environment.ulimit_core", "unlimited")
    if stack:
        lines.append(f"ulimit -s {shlex.quote(str(stack))}")
    if core:
        lines.append(f"ulimit -c {shlex.quote(str(core))}")

    lines.append(f"mkdir -p {shlex.quote(str(scratch))}")
    lines.append(f"export SCRATCH={shlex.quote(str(scratch))}")

    extra = cfg.section("environment_extra")
    lines.append(f"export OMP_NUM_THREADS={shlex.quote(str(extra.get('OMP_NUM_THREADS', '1')))}")
    lines.append(f"export MKL_NUM_THREADS={shlex.quote(str(extra.get('MKL_NUM_THREADS', '1')))}")
    for key, value in cfg.section("maxent_environment_extra").items():
        lines.append(f"export {key}={shlex.quote(str(value))}")

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
        maxent = shlex.quote(str(Path(edmft_root) / "maxent_run.py"))
        py = shlex.quote(python)
        launcher = _maxent_mpi_launcher(cfg)
        if launcher is None:
            # Match upstream to_real_axis.py: invoke maxent_run.py directly.
            # Remove stale MPI bootstrap variables so mpi4py starts as a clean
            # singleton communicator instead of interpreting another MPI stack.
            return [
                'echo "MaxEnt launch mode: direct Python (upstream-compatible; no external mpirun)"',
                "unset PMI_SIZE PMI_RANK PMI_FD PMIX_RANK OMPI_COMM_WORLD_SIZE OMPI_COMM_WORLD_RANK || true",
                f"{py} {maxent} sig.inpx > maxent.out 2>&1",
                "test -s Sig.out",
            ]

        npflag = str(cfg.get("maxent.mpi_np_flag", "-np"))
        return [
            'NP=$(wc -l < "$PBS_NODEFILE")',
            f"MAXENT_MPI={shlex.quote(launcher)}",
            'echo "MaxEnt launch mode: explicit mpi4py-compatible MPI"',
            'echo "MaxEnt MPI launcher=$MAXENT_MPI"',
            f"$MAXENT_MPI {shlex.quote(npflag)} \"$NP\" {py} {maxent} sig.inpx > maxent.out 2>&1",
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
    """Render a self-contained PBS script.

    The generated job has no workflow runtime dependency and contains only the
    resolved environment plus native WIEN2k/eDMFT commands, so it can be
    inspected and submitted manually.
    """
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
    if stage == "maxent" and _maxent_mpi_launcher(cfg) is None and ppn != 1:
        lines += [
            "# NOTE: MaxEnt is in upstream-compatible direct-Python mode.",
            f"# pbs_maxent.ppn={ppn} requests multiple slots but only one process is launched.",
            "# Set pbs_maxent.ppn=1, or explicitly configure maxent.mpi_launcher",
            "# to an MPI implementation that matches this Python's mpi4py build.",
        ]

    if stage == "maxent":
        preamble = _maxent_preamble(cfg, cwd, scratch)
    else:
        preamble = shell_preamble(cfg, cwd, scratch=scratch)

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
