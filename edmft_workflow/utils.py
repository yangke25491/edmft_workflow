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
            cp = subprocess.run(command, cwd=cwd, env=merged_env, shell=shell,
                                stdout=f, stderr=subprocess.STDOUT, text=True)
    else:
        cp = subprocess.run(command, cwd=cwd, env=merged_env, shell=shell,
                            text=True)
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
    """Copy named WIEN2k case files explicitly.

    dmft_copy.py intentionally does not guarantee that every WIEN2k potential file
    needed by a later LAPW1 invocation is present.  Keeping this explicit avoids
    hidden dependencies on a parent directory.
    """
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


def build_runtime_env(cfg, cwd: Path, scratch: Path | None = None) -> dict[str, str]:
    # For ordinary WIEN2k DFT runs we explicitly pass dft/tmp.  For Haule's
    # x_dmft.py/run_dmft.py, current upstream W2kEnvironment uses '.' internally,
    # so the stage working directory remains the effective eDMFT scratch location.
    effective_scratch = (scratch or cwd).resolve()
    effective_scratch.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {"SCRATCH": str(effective_scratch)}
    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    python_dir = cfg.get("environment.python_bin_dir")
    path_parts = []
    if edmft_root:
        path_parts.append(str(edmft_root))
        env["WIEN_DMFT_ROOT"] = str(edmft_root)
    if wienroot:
        path_parts.append(str(wienroot))
        env["WIENROOT"] = str(wienroot)
    if python_dir:
        path_parts.append(str(python_dir))
    if path_parts:
        env["PATH"] = ":".join(path_parts + [os.environ.get("PATH", "")])
    if edmft_root:
        env["PYTHONPATH"] = str(edmft_root) + ":" + os.environ.get("PYTHONPATH", "")
    for key, value in cfg.section("environment_extra").items():
        env[str(key)] = str(value)
    return env


def shell_preamble(cfg, cwd: Path, scratch: Path | None = None) -> str:
    effective_scratch = (scratch or cwd).resolve()
    lines = ["set -euo pipefail", f"cd {shlex.quote(str(cwd))}"]
    setup = cfg.get("environment.setup_script")
    if setup:
        lines.append(f"source {shlex.quote(str(setup))}")
    wienroot = cfg.get("environment.wienroot")
    edmft_root = cfg.get("environment.edmft_root")
    pybin = cfg.get("environment.python_bin_dir")
    if wienroot:
        lines.append(f"export WIENROOT={shlex.quote(str(wienroot))}")
    if edmft_root:
        lines.append(f"export WIEN_DMFT_ROOT={shlex.quote(str(edmft_root))}")
    path_parts = [p for p in (edmft_root, wienroot, pybin) if p]
    if path_parts:
        joined = ":".join(shlex.quote(str(p)) for p in path_parts)
        lines.append(f"export PATH={joined}:$PATH")
    if edmft_root:
        lines.append(f"export PYTHONPATH={shlex.quote(str(edmft_root))}:${{PYTHONPATH:-}}")
    lines.append("export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}")
    lines.append("export MKL_NUM_THREADS=${MKL_NUM_THREADS:-1}")
    lines.append(f"mkdir -p {shlex.quote(str(effective_scratch))}")
    lines.append(f"export SCRATCH={shlex.quote(str(effective_scratch))}")
    for key, value in cfg.section("environment_extra").items():
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
    """Run a stage command with the configured site setup script when provided."""
    env = build_runtime_env(cfg, cwd, scratch=scratch)
    setup = cfg.get("environment.setup_script")
    if setup:
        printable = command if isinstance(command, str) else " ".join(shlex.quote(x) for x in command)
        wrapped = f"source {shlex.quote(str(setup))} && {printable}"
        return run(["bash", "-lc", wrapped], cwd=cwd, env=env, log=log, check=check)
    return run(command, cwd=cwd, env=env, log=log, check=check)
