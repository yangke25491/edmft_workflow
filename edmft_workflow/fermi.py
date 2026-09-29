from __future__ import annotations

from pathlib import Path
import math
import shutil


RY2EV = 13.60569193


def read_fermi_level(path: Path) -> float | None:
    """Read a one-number EF.dat-style file in eV without raising."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        value = float(path.read_text(errors="ignore").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return value if math.isfinite(value) else None


def dft_fermi_from_scf2(path: Path) -> float | None:
    """Return the last WIEN2k :FER value from case.scf2 converted Ry -> eV."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        ef_ry = None
        for line in path.read_text(errors="ignore").splitlines():
            if line.lstrip().startswith(":FER"):
                try:
                    ef_ry = float(line.split()[-1])
                except (ValueError, IndexError):
                    continue
        if ef_ry is None or not math.isfinite(ef_ry):
            return None
        return ef_ry * RY2EV
    except OSError:
        return None


def final_info_iterate_mu(path: Path) -> float | None:
    """Read the last parseable chemical potential from info.iterate in eV."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        mu = None
        for line in path.read_text(errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            fields = s.split()
            if len(fields) < 4:
                continue
            try:
                candidate = float(fields[3])
            except ValueError:
                continue
            if math.isfinite(candidate):
                mu = candidate
        return mu
    except OSError:
        return None


def copy_fermi_snapshot(source: Path, target: Path) -> float | None:
    """Copy an EF.dat value as a stage-local snapshot; missing/invalid is non-fatal."""
    value = read_fermi_level(source)
    if value is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return value


def _tol(cfg) -> float:
    try:
        return max(0.0, float(cfg.get("fermi.tolerance_eV", 1e-6)))
    except (TypeError, ValueError):
        return 1e-6


def _compare(label: str, a_path: Path, b_path: Path, tol: float) -> str | None:
    a = read_fermi_level(a_path)
    b = read_fermi_level(b_path)
    if a is None:
        return f"{label}: missing/unreadable {a_path}"
    if b is None:
        return f"{label}: missing/unreadable {b_path}"
    diff = abs(a - b)
    if diff > tol:
        return (
            f"{label}: mismatch {a_path.name}={a:.12f} eV, "
            f"{b_path.name}={b:.12f} eV, |dEF|={diff:.6g} eV > {tol:.3g} eV"
        )
    return None


def fermi_warnings(cfg, stage: str) -> list[str]:
    """Return warning-only Fermi-level consistency diagnostics.

    These checks intentionally never block preparation, PBS generation, or
    foreground execution.  They are meant to expose stale/mixed EF states.
    """
    warnings: list[str] = []
    tol = _tol(cfg)
    case = cfg.case
    dft = cfg.dft_dir
    dmft = cfg.dmft_dir
    dmft_ef = dmft / "EF.dat"

    if stage == "dft":
        stray = dft / "EF.dat"
        if stray.exists():
            msg = (
                f"{stray} exists. Haule eDMFT gives EF.dat priority over case.scf2 :FER; "
                "verify this file is intentional before prepare-dmft."
            )
            dft_scf_ef = dft_fermi_from_scf2(dft / f"{case}.scf2")
            stray_ef = read_fermi_level(stray)
            if dft_scf_ef is not None and stray_ef is not None:
                msg += (
                    f" DFT :FER={dft_scf_ef:.12f} eV, EF.dat={stray_ef:.12f} eV, "
                    f"difference={abs(dft_scf_ef-stray_ef):.6g} eV."
                )
            warnings.append(msg)
        return warnings

    if stage == "dmft":
        ef = read_fermi_level(dmft_ef)
        if ef is None:
            warnings.append(
                f"DMFT canonical chemical potential is missing/unreadable: {dmft_ef}. "
                "eDMFT may fall back to case.scf2 :FER."
            )
        mu = final_info_iterate_mu(dmft / "info.iterate")
        if ef is not None and mu is not None and abs(ef - mu) > tol:
            warnings.append(
                f"dmft/EF.dat={ef:.12f} eV differs from final info.iterate mu={mu:.12f} eV "
                f"by {abs(ef-mu):.6g} eV > {tol:.3g} eV."
            )
        elif ef is not None and mu is None:
            warnings.append("Could not parse final chemical potential from dmft/info.iterate for cross-check.")
        return warnings

    maxent = dmft / "maxent"
    maxent_snapshot = maxent / "fermi_level.snapshot"

    if stage == "maxent":
        msg = _compare("MaxEnt self-energy context", dmft_ef, maxent_snapshot, tol)
        if msg:
            warnings.append(msg)
        return warnings

    if stage in {"dos", "band"}:
        root = dmft / ("onreal" if stage == "dos" else "band")
        stage_ef = root / "EF.dat"
        stage_snapshot = root / "fermi_level.snapshot"
        maxent_stage_snapshot = root / "maxent_fermi_level.snapshot"

        for label, a, b in [
            (f"{stage.upper()} vs current DMFT", dmft_ef, stage_ef),
            (f"{stage.upper()} prepare snapshot vs current DMFT", dmft_ef, stage_snapshot),
            (f"{stage.upper()} self-energy EF context", stage_ef, maxent_stage_snapshot),
        ]:
            msg = _compare(label, a, b, tol)
            if msg:
                warnings.append(msg)

        # Also compare directly against MaxEnt's original snapshot when present.
        if maxent_snapshot.exists():
            msg = _compare(f"{stage.upper()} vs MaxEnt source context", stage_ef, maxent_snapshot, tol)
            if msg:
                warnings.append(msg)
        return warnings

    return warnings
