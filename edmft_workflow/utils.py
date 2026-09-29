from __future__ import annotations

from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess
import time
from typing import Sequence


class WorkflowError(RuntimeError):
    pass


def require_file(path: Path, nonempty: bool = True) -> Path:
    if not path.exists():
        raise WorkflowError(f"Required file is missing: {path}")
    if nonempty and path.stat().st_size == 0:
        raise WorkflowError(f"Required file is empty: {path}")
    return path


def which_or_path(command: str) -> str:
    p = Path(os.path.expandvars(os.path.expanduser(command)))
    if p.is_file():
        return str(p)
    found = shutil.which(command)
    if not found:
        raise WorkflowError(f"Executable not found: {command}")
    return found


def run(
    command: Sequence[str] | str,
    cwd: Path,
    env: dict[str, str] | None = None,
    log: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run one child process without mutating the caller's shell environment."""
    cwd.mkdir(parents=True, exist_ok=True)
    shell = isinstance(command, str)
    printable = command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
    print(f"[run] {printable}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update({k: str(v) for k, v in env.items()})

    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n$ {printable}\n")
            cp = subprocess.run(
                command,
                cwd=cwd,
                env=merged_env,
                shell=shell,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
    else:
        cp = subprocess.run(command, cwd=cwd, env=merged_env, shell=shell, text=True)
    if check and cp.returncode != 0:
        raise WorkflowError(f"Command failed with exit code {cp.returncode}: {printable}")
    return cp


def safe_prepare_dir(path: Path, force: bool = False) -> Path:
    if path.exists() and any(path.iterdir()):
        if not force:
            raise WorkflowError(
                f"Directory already exists and is not empty: {path}. "
                "Use --force to back it up and recreate it."
            )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(path.name + f".bak.{stamp}")
        path.rename(backup)
        print(f"[backup] {path} -> {backup}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def patch_indmfl(path: Path, matsubara: int, nomega: int, wmin: float, wmax: float) -> None:
    require_file(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise WorkflowError(f"Malformed indmfl file: {path}")
    fields = lines[1].split()
    if len(fields) < 6:
        raise WorkflowError(f"Second line of {path} has fewer than 6 fields")
    fields[0] = str(int(matsubara))
    fields[3] = str(int(nomega))
    fields[4] = f"{float(wmin):.8f}"
    fields[5] = f"{float(wmax):.8f}"
    lines[1] = " ".join(fields)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def count_klist_points(path: Path) -> int:
    require_file(path)
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line[:3].strip().upper() == "END" or line.strip() == "END":
                break
            if line.strip():
                n += 1
    return n


def grep_int(path: Path, pattern: str) -> int | None:
    if not path.exists():
        return None
    rgx = re.compile(pattern)
    for line in path.read_text(errors="ignore").splitlines():
        m = rgx.search(line)
        if m:
            return int(m.group(1))
    return None


def assert_log_contains(path: Path, marker: str) -> None:
    require_file(path, nonempty=False)
    text = path.read_text(errors="ignore")
    if marker not in text:
        raise WorkflowError(f"Success marker {marker!r} not found in {path}")


def copy_case_files(source: Path, target: Path, case: str, suffixes: Sequence[str], required: bool = True) -> None:
    """Copy named WIEN2k case files explicitly."""
    target.mkdir(parents=True, exist_ok=True)
    for suffix in suffixes:
        src = source / f"{case}.{suffix}"
        if not src.exists():
            if required:
                raise WorkflowError(f"Required WIEN2k file is missing: {src}")
            continue
        if src.stat().st_size == 0:
            if required:
                raise WorkflowError(f"Required WIEN2k file is empty: {src}")
            continue
        shutil.copy2(src, target / src.name)


def edmft_helper_command(cfg, name: str) -> list[str]:
    """Resolve a lightweight upstream helper to an explicit command.

    Preparation helpers deliberately do not depend on PATH activation. By
    default they use the configured Python interpreter plus the absolute eDMFT
    script path. A commands.<name> override remains available for unusual local
    installations.
    """
    key = name.replace(".py", "").replace("-", "_")
    configured = cfg.get(f"commands.{key}")
    if configured:
        if isinstance(configured, str):
            tokens = shlex.split(configured)
        elif isinstance(configured, (list, tuple)):
            tokens = [str(x) for x in configured]
        else:
            raise WorkflowError(f"commands.{key} must be a string or list")
        if not tokens:
            raise WorkflowError(f"commands.{key} is empty")
        return tokens

    python = cfg.get("environment.python")
    root = cfg.get("environment.edmft_root")
    if not python or not root:
        raise WorkflowError(
            f"environment.python and environment.edmft_root are required to run {name}"
        )
    script = Path(str(root)) / name
    return [str(python), str(script)]


def build_helper_env(cfg, scratch: Path | None = None) -> dict[str, str]:
    """Return only process-local variables needed by lightweight eDMFT helpers.

    No Intel/MKL/MPI setup, PATH rewrite, LD_LIBRARY_PATH rewrite, PYTHONPATH
    rewrite, or environment_extra values are injected here. ``run`` merges
    these overrides into a copy of the current process environment, so the
    caller's login shell is never modified.
    """
    env: dict[str, str] = {}
    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    if wienroot:
        env["WIENROOT"] = str(wienroot)
    if edmft_root:
        env["WIEN_DMFT_ROOT"] = str(edmft_root)
    if scratch is not None:
        scratch = scratch.resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        env["SCRATCH"] = str(scratch)
    return env


def run_edmft_helper(
    cfg,
    name: str,
    args: Sequence[str] | None,
    cwd: Path,
    log: Path | None = None,
    check: bool = True,
    scratch: Path | None = None,
):
    """Run a small official eDMFT helper with minimal child-only environment."""
    cmd = [*edmft_helper_command(cfg, name), *[str(x) for x in (args or [])]]
    return run(
        cmd,
        cwd=cwd,
        env=build_helper_env(cfg, scratch=scratch),
        log=log,
        check=check,
    )


def _python_env_root(cfg) -> Path | None:
    py = cfg.get("environment.python")
    if py:
        p = Path(str(py)).expanduser()
        if p.is_absolute() and p.parent.name == "bin":
            return p.parent.parent
    pybin = cfg.get("environment.python_bin_dir")
    if pybin:
        p = Path(str(pybin)).expanduser()
        if p.name == "bin":
            return p.parent
    return None


def build_runtime_env(cfg, cwd: Path, scratch: Path | None = None) -> dict[str, str]:
    """Build the full heavy-job environment for legacy foreground runners."""
    effective_scratch = (scratch or cwd).resolve()
    effective_scratch.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {"SCRATCH": str(effective_scratch)}
    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    python_dir = cfg.get("environment.python_bin_dir")
    if edmft_root:
        env["WIEN_DMFT_ROOT"] = str(edmft_root)
    if wienroot:
        env["WIENROOT"] = str(wienroot)
    path_parts = [str(x) for x in (edmft_root, wienroot, python_dir) if x]
    if path_parts:
        env["PATH"] = ":".join(path_parts + [os.environ.get("PATH", "")])
    if edmft_root:
        env["PYTHONPATH"] = str(edmft_root) + (
            ":" + os.environ.get("PYTHONPATH", "") if os.environ.get("PYTHONPATH") else ""
        )
    for key, value in cfg.section("environment_extra").items():
        env[str(key)] = str(value)
    return env


def shell_preamble(cfg, cwd: Path, scratch: Path | None = None) -> str:
    """Render the complete runtime setup for standalone heavy jobs.

    This is intentionally used by generated PBS scripts (and retained legacy
    foreground numerical runners), not by prepare-* helpers. The generated
    shell reproduces the validated Intel/MKL/MPI/WIEN2k/eDMFT runtime while the
    user's current login shell remains untouched.
    """
    effective_scratch = (scratch or cwd).resolve()
    lines = ["set -e", f"cd {shlex.quote(str(cwd))}"]

    setup = cfg.get("environment.setup_script")
    if setup:
        lines.append(f"source {shlex.quote(str(setup))}")

    intel_root = cfg.get("environment.intel_root")
    intel_arch = str(cfg.get("environment.intel_arch", "intel64"))
    if intel_root:
        intel = Path(str(intel_root)).expanduser()
        lines.append(f"INTEL={shlex.quote(str(intel))}")
        lines.append(f"source \"$INTEL/linux/bin/compilervars.sh\" {shlex.quote(intel_arch)}")

    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    pybin = cfg.get("environment.python_bin_dir")
    pyenv = _python_env_root(cfg)
    fftw_lib = cfg.get("environment.fftw_lib")

    if wienroot:
        lines.append(f"export WIENROOT={shlex.quote(str(wienroot))}")
    if edmft_root:
        lines.append(f"export WIEN_DMFT_ROOT={shlex.quote(str(edmft_root))}")

    if intel_root:
        intel = Path(str(intel_root)).expanduser()
        path_parts = [
            edmft_root,
            wienroot,
            str(intel / "linux/mpi/intel64/bin"),
            str(intel / "linux/bin/intel64"),
            pybin,
            "/usr/bin",
            "/bin",
        ]
        path_text = ":".join(str(x) for x in path_parts if x)
        lines.append(f"export PATH={shlex.quote(path_text)}")

        ld_parts = [
            str(intel / "linux/mkl/lib/intel64"),
            str(intel / "linux/compiler/lib/intel64_lin"),
            str(intel / "linux/mpi/intel64/lib/release"),
            str(intel / "linux/mpi/intel64/lib"),
        ]
        if fftw_lib:
            ld_parts.append(str(fftw_lib))
        if pyenv:
            ld_parts.append(str(pyenv / "lib"))
        ld_text = ":".join(ld_parts)
        lines.append(f"export LD_LIBRARY_PATH={shlex.quote(ld_text)}:${{LD_LIBRARY_PATH:-}}")
    else:
        path_parts = [p for p in (edmft_root, wienroot, pybin) if p]
        if path_parts:
            joined = ":".join(str(p) for p in path_parts)
            lines.append(f"export PATH={shlex.quote(joined)}:$PATH")

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

    lines.append(f"mkdir -p {shlex.quote(str(effective_scratch))}")
    lines.append(f"export SCRATCH={shlex.quote(str(effective_scratch))}")

    extra = cfg.section("environment_extra")
    if "OMP_NUM_THREADS" not in extra:
        lines.append("export OMP_NUM_THREADS=1")
    if "MKL_NUM_THREADS" not in extra:
        lines.append("export MKL_NUM_THREADS=1")
    for key, value in extra.items():
        lines.append(f"export {key}={shlex.quote(str(value))}")
    return "\n".join(lines)


def run_stage(
    cfg,
    command: Sequence[str] | str,
    cwd: Path,
    log: Path | None = None,
    check: bool = True,
    scratch: Path | None = None,
):
    """Legacy foreground numerical runner using the full configured runtime."""
    env = build_runtime_env(cfg, cwd, scratch=scratch)
    setup = cfg.get("environment.setup_script")
    intel_root = cfg.get("environment.intel_root")
    if setup or intel_root:
        printable = command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
        wrapped = shell_preamble(cfg, cwd, scratch=scratch) + "\n" + printable
        return run(["bash", "-lc", wrapped], cwd=cwd, env=env, log=log, check=check)
    return run(command, cwd=cwd, env=env, log=log, check=check)
