from __future__ import annotations

from pathlib import Path
import shlex
import subprocess

from .utils import WorkflowError


def _wrapped_script(cfg, body: str) -> str:
    lines = ["set -e"]
    setup = cfg.get("environment.setup_script")
    if setup:
        lines.append(f"source {shlex.quote(str(setup))}")
    else:
        # Even without a setup script, export the configured roots so the
        # diagnostic reflects the same assumptions used by the workflow.
        if cfg.get("environment.wienroot"):
            lines.append(f"export WIENROOT={shlex.quote(str(cfg.get('environment.wienroot')))}")
        if cfg.get("environment.edmft_root"):
            lines.append(f"export WIEN_DMFT_ROOT={shlex.quote(str(cfg.get('environment.edmft_root')))}")
    lines.append(body)
    return "\n".join(lines)


def _run_shell(cfg, body: str) -> tuple[int, str]:
    cp = subprocess.run(
        ["bash", "-lc", _wrapped_script(cfg, body)],
        text=True,
        capture_output=True,
    )
    text = (cp.stdout + cp.stderr).strip()
    return cp.returncode, text


def environment_report(cfg) -> list[tuple[str, bool, str]]:
    """Validate the runtime stack used by PBS and foreground numerical stages.

    The main purpose is to catch the failures already seen in real eDMFT runs:
    wrong MPI launcher, mpi4py built against another MPI, and unresolved Intel
    MKL shared libraries in ctqmc/dmft executables.
    """
    checks: list[tuple[str, bool, str]] = []

    setup = cfg.get("environment.setup_script")
    if setup:
        p = Path(str(setup)).expanduser()
        checks.append(("environment.setup_script", p.is_file(), str(p)))

    commands = [
        ("WIENROOT", 'test -n "$WIENROOT" && test -d "$WIENROOT" && printf "%s" "$WIENROOT"'),
        ("WIEN_DMFT_ROOT", 'test -n "$WIEN_DMFT_ROOT" && test -d "$WIEN_DMFT_ROOT" && printf "%s" "$WIEN_DMFT_ROOT"'),
        ("mpirun", 'command -v mpirun'),
        ("MPI version", 'mpirun -V 2>&1 | head -n 2'),
    ]
    for name, cmd in commands:
        rc, text = _run_shell(cfg, cmd)
        checks.append((name, rc == 0, text or "not found"))

    py = str(cfg.get("environment.python", "python"))
    rc, text = _run_shell(
        cfg,
        f"{shlex.quote(py)} -c 'from mpi4py import MPI; print(MPI.Get_library_version().strip())'",
    )
    checks.append(("mpi4py", rc == 0, text or "import failed"))

    root = cfg.get("environment.edmft_root")
    if root:
        for exe in ("ctqmc", "dmft", "dmft2"):
            path = Path(str(root)) / exe
            if not path.is_file():
                checks.append((f"{exe} executable", False, f"missing: {path}"))
                continue
            rc, text = _run_shell(cfg, f"ldd {shlex.quote(str(path))} 2>&1")
            missing = [line.strip() for line in text.splitlines() if "not found" in line]
            ok = rc == 0 and not missing
            detail = "; ".join(missing) if missing else "all shared libraries resolved"
            checks.append((f"ldd {exe}", ok, detail))

    # Explicitly confirm the three MKL libraries that previously caused runtime
    # failures are visible to the dynamic linker through the configured setup.
    rc, text = _run_shell(
        cfg,
        "python - <<'PY'\n"
        "import ctypes\n"
        "libs=['libmkl_intel_lp64.so','libmkl_intel_thread.so','libmkl_core.so']\n"
        "bad=[]\n"
        "for lib in libs:\n"
        "    try: ctypes.CDLL(lib)\n"
        "    except OSError as e: bad.append(f'{lib}: {e}')\n"
        "print('OK' if not bad else '\\n'.join(bad))\n"
        "raise SystemExit(0 if not bad else 1)\n"
        "PY",
    )
    checks.append(("Intel MKL runtime", rc == 0, text or "MKL load test failed"))
    return checks


def print_environment_report(cfg) -> bool:
    checks = environment_report(cfg)
    all_ok = True
    for name, ok, detail in checks:
        print(f"{'OK' if ok else 'FAIL':4s}  {name:24s}  {detail}")
        all_ok &= ok
    return all_ok


def require_environment(cfg) -> None:
    if not print_environment_report(cfg):
        raise WorkflowError("Runtime environment preflight failed; fix MPI/MKL/Python setup before submitting compute jobs.")
