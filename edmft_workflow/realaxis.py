from __future__ import annotations

from pathlib import Path
import difflib
import shutil

from .utils import (
    WorkflowError, copy_case_files, patch_indmfl, require_file,
    run_stage, safe_prepare_dir,
)


def _edmft_command(cfg, name: str) -> str:
    key = name.replace(".py", "").replace("-", "_")
    configured = cfg.get(f"commands.{key}")
    if configured:
        return str(configured)
    root = cfg.get("environment.edmft_root")
    if not root:
        raise WorkflowError(f"environment.edmft_root is required to locate {name}")
    return str(Path(str(root)) / name)


def _copy_wien_potentials(cfg, out: Path) -> None:
    """Copy converged potentials so real-axis work cannot modify dmft/."""
    case = cfg.case
    copy_case_files(cfg.dmft_dir, out, case, ["vsp", "vns"], required=False)
    copy_case_files(
        cfg.dmft_dir, out, case,
        ["vspup", "vspdn", "vnsup", "vnsdn", "vrespsum"], required=False,
    )
    base_ok = all((out / f"{case}.{s}").is_file() and (out / f"{case}.{s}").stat().st_size > 0
                  for s in ("vsp", "vns"))
    spin_ok = all((out / f"{case}.{s}").is_file() and (out / f"{case}.{s}").stat().st_size > 0
                  for s in ("vspup", "vspdn", "vnsup", "vnsdn"))
    if not (base_ok or spin_ok):
        raise WorkflowError(
            "Real-axis directory needs converged potential files: case.vsp+case.vns "
            "or vspup/vspdn/vnsup/vnsdn."
        )


def _indmfl_flag(path: Path) -> int:
    require_file(path)
    lines = path.read_text(errors="ignore").splitlines()
    if len(lines) < 2 or not lines[1].split():
        raise WorkflowError(f"Malformed indmfl file: {path}")
    try:
        return int(lines[1].split()[0])
    except ValueError as exc:
        raise WorkflowError(f"Invalid Matsubara flag in {path}: {lines[1]}") from exc


def _prepare_real_axis_indmfl(path: Path, nomega: int, wmin: float, wmax: float) -> Path:
    """Preserve the Matsubara input and make the documented 1 -> 0 switch."""
    require_file(path)
    before = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if _indmfl_flag(path) != 1:
        raise WorkflowError(
            f"Expected Matsubara flag 1 before real-axis conversion, found {_indmfl_flag(path)} in {path}"
        )

    backup = path.with_name(path.name + ".matsubara")
    shutil.copy2(path, backup)
    patch_indmfl(path, matsubara=0, nomega=nomega, wmin=wmin, wmax=wmax)
    if _indmfl_flag(path) != 0:
        raise WorkflowError(f"Failed to switch Matsubara flag from 1 to 0 in {path}")

    after = path.read_text(encoding="utf-8").splitlines(keepends=True)
    diff = path.parent / "indmfl.diff"
    diff.write_text(
        "".join(difflib.unified_diff(before, after, fromfile=backup.name, tofile=path.name)),
        encoding="utf-8",
    )
    return backup


def prepare_dos(cfg, force: bool = False) -> Path:
    """Prepare a standalone real-axis DOS directory; do not run numerical jobs."""
    case = cfg.case
    source = cfg.dmft_dir
    require_file(source / f"{case}.indmfl")
    require_file(source / "info.iterate")
    sig = require_file(source / "maxent" / "Sig.out")

    out = safe_prepare_dir(source / "onreal", force=force)
    dmft_copy = _edmft_command(cfg, "dmft_copy.py")

    # Official semantics: dmft_copy.py SOURCE copies SOURCE into cwd.
    run_stage(cfg, [dmft_copy, str(source)], cwd=out, log=out / "dmft_copy.log")
    _copy_wien_potentials(cfg, out)

    # The continued real-axis self-energy is named sig.inp for dmft1/dmftp.
    shutil.copy2(sig, out / "sig.inp")

    indmfl = require_file(out / f"{case}.indmfl")
    backup = _prepare_real_axis_indmfl(
        indmfl,
        nomega=int(cfg.get("dos.nomega", 200)),
        wmin=float(cfg.get("dos.wmin", -3.0)),
        wmax=float(cfg.get("dos.wmax", 1.0)),
    )

    (out / "prepare.log").write_text(
        "\n".join([
            f"source_dmft={source}",
            f"self_energy_source={sig}",
            f"self_energy_target={out / 'sig.inp'}",
            f"indmfl_backup={backup}",
            "matsubara_flag=0",
            f"nomega={int(cfg.get('dos.nomega', 200))}",
            f"wmin={float(cfg.get('dos.wmin', -3.0))}",
            f"wmax={float(cfg.get('dos.wmax', 1.0))}",
        ]) + "\n",
        encoding="utf-8",
    )

    print(f"Real-axis DOS directory prepared: {out}")
    print(f"Self-energy: {sig} -> {out / 'sig.inp'}")
    print(f"indmfl Matsubara backup: {backup}")
    print("indmfl Matsubara flag: 1 -> 0")
    print(f"Review changes: {out / 'indmfl.diff'}")
    return out
