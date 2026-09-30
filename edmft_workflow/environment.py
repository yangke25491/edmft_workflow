from __future__ import annotations

from pathlib import Path
import shlex
import subprocess

from .utils import WorkflowError, shell_preamble


def _wrapped_script(cfg, body: str) -> str:
    cwd = Path.cwd().resolve()
    scratch = cwd / ".edmft_workflow_preflight_tmp"
    return shell_preamble(cfg, cwd, scratch=scratch) + "\n" + body


def _run_shell(cfg, body: str) -> tuple[int, str]:
    cp = subprocess.run(
        ["bash", "-lc", _wrapped_script(cfg, body)],
        text=True,
        capture_output=True,
    )
    text = (cp.stdout + cp.stderr).strip()
    return cp.returncode, text


def _site_shared_libraries(cfg) -> list[str]:
    if not cfg.site:
        return []
    checks = cfg.site.get("checks", {})
    if not isinstance(checks, dict):
        return []
    libs = checks.get("shared_libraries", [])
    if isinstance(libs, str):
        return [libs]
    return [str(x) for x in libs]


def environment_report(cfg) -> list[tuple[str, bool, str]]:
    """Validate the machine profile used by foreground and generated PBS jobs."""
    checks: list[tuple[str, bool, str]] = []

    if cfg.site_source:
        checks.append(("site profile", True, str(cfg.site_source)))
    else:
        checks.append(("site profile", True, "not loaded; using legacy config sections"))

    for key, label in (
        ("environment.wienroot", "WIENROOT directory"),
        ("environment.edmft_root", "eDMFT directory"),
    ):
        raw = cfg.get(key)
        if raw:
            p = Path(str(raw)).expanduser()
            checks.append((label, p.is_dir(), str(p)))
        else:
            checks.append((label, False, f"missing machine setting: {key}"))

    py = str(cfg.get("environment.python", "python"))
    py_path = Path(py).expanduser()
    py_ok = py_path.is_file() if py_path.is_absolute() else True
    checks.append(("Python", py_ok, py))

    rc, text = _run_shell(cfg, "true")
    checks.append(("runtime setup", rc == 0, text or "setup commands completed"))

    mpi = str(cfg.get("parallel.mpi_launcher", "mpirun"))
    rc, text = _run_shell(cfg, f"{shlex.quote(mpi)} -V 2>&1 | head -n 2")
    checks.append(("native MPI", rc == 0, text or mpi))

    pyq = shlex.quote(py)
    rc, text = _run_shell(
        cfg,
        f"{pyq} -c 'from mpi4py import MPI; print(MPI.Get_library_version().strip())'",
    )
    checks.append(("mpi4py", rc == 0, text or "import failed"))

    maxent_mpi = cfg.get("maxent.mpi_launcher")
    if maxent_mpi:
        rc, text = _run_shell(cfg, f"{shlex.quote(str(maxent_mpi))} --version 2>&1 | head -n 2")
        checks.append(("MaxEnt MPI", rc == 0, text or str(maxent_mpi)))

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

    libraries = _site_shared_libraries(cfg)
    if libraries:
        payload = repr(libraries)
        rc, text = _run_shell(
            cfg,
            f"{pyq} - <<'PY'\n"
            "import ctypes\n"
            f"libs={payload}\n"
            "bad=[]\n"
            "for lib in libs:\n"
            "    try: ctypes.CDLL(lib)\n"
            "    except OSError as e: bad.append(f'{lib}: {e}')\n"
            "print('OK' if not bad else '\\n'.join(bad))\n"
            "raise SystemExit(0 if not bad else 1)\n"
            "PY",
        )
        checks.append(("runtime libraries", rc == 0, text or "library load test failed"))

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
        raise WorkflowError("Runtime environment preflight failed; fix site.toml before submitting compute jobs.")
