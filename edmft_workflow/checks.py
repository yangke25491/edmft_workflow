from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Iterable

import numpy as np

from .provenance import verify_stage_manifest
from .utils import WorkflowError, require_file, count_klist_points, grep_int


@dataclass
class IterateRow:
    step: int
    outer: int
    inner: int
    mu: float
    vdc: float
    e1: float
    e2: float
    e3: float
    n_latt: float
    n_imp: float
    tail: list[float]

    @property
    def dn(self) -> float:
        return abs(self.n_latt - self.n_imp)


def parse_info_iterate(path: Path) -> list[IterateRow]:
    require_file(path)
    rows: list[IterateRow] = []
    for line in path.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        fields = s.split()
        if len(fields) < 10:
            continue
        try:
            step = int(float(fields[0]))
            outer = int(float(fields[1]))
            inner = int(float(fields[2]))
            vals = [float(x) for x in fields[3:]]
        except ValueError:
            continue
        if len(vals) < 7:
            continue
        rows.append(
            IterateRow(
                step=step,
                outer=outer,
                inner=inner,
                mu=vals[0],
                vdc=vals[1],
                e1=vals[2],
                e2=vals[3],
                e3=vals[4],
                n_latt=vals[5],
                n_imp=vals[6],
                tail=vals[7:],
            )
        )
    if not rows:
        raise WorkflowError(f"Could not parse any iterations from {path}")
    return rows


def last_of_each_outer(rows: Iterable[IterateRow]) -> list[IterateRow]:
    by_outer: dict[int, IterateRow] = {}
    for row in rows:
        if row.outer not in by_outer or row.inner >= by_outer[row.outer].inner:
            by_outer[row.outer] = row
    return [by_outer[k] for k in sorted(by_outer)]


def convergence_report(dmft_dir: Path, max_dn: float = 5e-3, drift_tol: float = 5e-3) -> dict:
    rows = parse_info_iterate(dmft_dir / "info.iterate")
    outer = last_of_each_outer(rows)
    last = outer[-1]
    previous = outer[-2] if len(outer) >= 2 else None
    drift_n_latt = abs(last.n_latt - previous.n_latt) if previous else math.nan
    drift_n_imp = abs(last.n_imp - previous.n_imp) if previous else math.nan
    drift_mu = abs(last.mu - previous.mu) if previous else math.nan

    pass_dn = last.dn <= max_dn
    pass_drift = True
    if previous:
        pass_drift = max(drift_n_latt, drift_n_imp) <= drift_tol

    return {
        "last": last,
        "previous": previous,
        "outer_rows": outer,
        "drift_n_latt": drift_n_latt,
        "drift_n_imp": drift_n_imp,
        "drift_mu": drift_mu,
        "pass_dn": pass_dn,
        "pass_drift": pass_drift,
        "pass": pass_dn and pass_drift,
        "max_dn": max_dn,
        "drift_tol": drift_tol,
    }


def format_convergence(report: dict) -> str:
    r: IterateRow = report["last"]
    status = "PASS" if report["pass"] else "WARNING"
    lines = [
        f"DMFT convergence: {status}",
        f"last outer cycle       : {r.outer}",
        f"last charge iteration  : {r.inner}",
        f"mu                     : {r.mu:.9f} eV",
        f"Vdc                    : {r.vdc:.9f} eV",
        f"n_latt                 : {r.n_latt:.9f}",
        f"n_imp                  : {r.n_imp:.9f}",
        f"|n_latt-n_imp|         : {r.dn:.9f} (threshold {report['max_dn']:.3g})",
    ]
    if report["previous"]:
        lines += [
            f"outer drift n_latt     : {report['drift_n_latt']:.9g}",
            f"outer drift n_imp      : {report['drift_n_imp']:.9g}",
            f"outer drift mu         : {report['drift_mu']:.9g} eV",
        ]
    return "\n".join(lines)


def _file_check(name: str, path: Path) -> tuple[str, bool, str]:
    ok = path.exists() and path.is_file() and path.stat().st_size > 0
    return name, ok, str(path)


def _any_file_check(name: str, paths: list[Path]) -> tuple[str, bool, str]:
    found = [p for p in paths if p.is_file() and p.stat().st_size > 0]
    return name, bool(found), str(found[0]) if found else " | ".join(str(p) for p in paths)


def _indmfl_flag(path: Path) -> int | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    lines = path.read_text(errors="ignore").splitlines()
    if len(lines) < 2:
        return None
    try:
        return int(lines[1].split()[0])
    except (ValueError, IndexError):
        return None


def _sigma_table_check(name: str, path: Path) -> tuple[str, bool, str]:
    if not path.is_file() or path.stat().st_size == 0:
        return name, False, str(path)
    try:
        data = np.loadtxt(path, comments="#")
    except (OSError, ValueError) as exc:
        return name, False, f"parse error: {exc}"
    if data.ndim != 2 or data.shape[1] < 3 or (data.shape[1] - 1) % 2:
        return name, False, f"unexpected shape={data.shape}"
    if not np.all(np.isfinite(data)):
        return name, False, "contains NaN/Inf"
    return name, True, f"points={data.shape[0]} channels={(data.shape[1]-1)//2}"


def _manifest_check(root: Path) -> tuple[str, bool, str]:
    ok, detail = verify_stage_manifest(root)
    return "manifest snapshot", ok, detail


def _optional_pbs(checks: list[tuple[str, bool, str]], path: Path) -> None:
    """Validate a PBS script if it has already been frozen, but do not require it during prepare review."""
    if path.exists():
        checks.append(_file_check(path.name, path))


def doctor_dft(cfg) -> list[tuple[str, bool, str]]:
    root = cfg.dft_dir
    case = cfg.case
    checks = [
        _file_check("case.struct", root / f"{case}.struct"),
        _file_check("case.in0", root / f"{case}.in0"),
        _any_file_check("case.in1/in1c", [root / f"{case}.in1", root / f"{case}.in1c"]),
        _any_file_check("case.in2/in2c", [root / f"{case}.in2", root / f"{case}.in2c"]),
        _file_check("case.klist", root / f"{case}.klist"),
        _file_check("case.scf", root / f"{case}.scf"),
        _file_check("case.indmf", root / f"{case}.indmf"),
        _file_check("case.indmfl", root / f"{case}.indmfl"),
        _file_check("case.indmfi", root / f"{case}.indmfi"),
    ]
    indmfl = root / f"{case}.indmfl"
    if indmfl.exists():
        checks.append(("case.indmfl flag", _indmfl_flag(indmfl) == 1, f"found={_indmfl_flag(indmfl)} expected=1"))
    _optional_pbs(checks, root / "run_dft.pbs")
    return checks


def doctor_dmft(cfg) -> list[tuple[str, bool, str]]:
    dmft = cfg.dmft_dir
    case = cfg.case
    checks: list[tuple[str, bool, str]] = []
    for rel in [
        f"{case}.struct", f"{case}.indmfl", f"{case}.indmfi", "params.dat", "sig.inp",
        "projectorw.dat", "info.iterate",
    ]:
        checks.append(_file_check(rel, dmft / rel))
    indmfl = dmft / f"{case}.indmfl"
    checks.append(("case.indmfl flag", _indmfl_flag(indmfl) == 1, f"found={_indmfl_flag(indmfl)} expected=1"))
    sigs = sorted(dmft.glob("sig.inp.*.*"))
    checks.append(("sig.inp.*.*", bool(sigs), f"{len(sigs)} files"))
    impurities = sorted(p for p in dmft.glob("imp.*") if p.is_dir())
    checks.append(("imp.*/", bool(impurities), f"{len(impurities)} directories"))
    _optional_pbs(checks, dmft / "run_dmft.pbs")
    return checks


def doctor_maxent(cfg) -> list[tuple[str, bool, str]]:
    root = cfg.dmft_dir / "maxent"
    checks = [
        _file_check("selected_sigmas.txt", root / "selected_sigmas.txt"),
        _sigma_table_check("Sig.average", root / "Sig.average"),
        _file_check("maxent_params.dat", root / "maxent_params.dat"),
        _manifest_check(root),
    ]
    _optional_pbs(checks, root / "run_maxent.pbs")
    sigout = root / "Sig.out"
    if sigout.exists():
        checks.append(_sigma_table_check("Sig.out", sigout))
    return checks


def _dmft1_end(root: Path, case: str) -> tuple[bool, str]:
    candidates = [root / "dmft1.log", root / f"{case}.outputdmf1"]
    for path in candidates:
        if path.exists() and "DMFT1 END" in path.read_text(errors="ignore"):
            return True, str(path)
    return False, "DMFT1 END not found in dmft1.log or case.outputdmf1"


def doctor_dos(cfg) -> list[tuple[str, bool, str]]:
    root = cfg.dmft_dir / "onreal"
    case = cfg.case
    live = root / f"{case}.indmfl"
    backup = root / f"{case}.indmfl.matsubara"
    checks = [
        _sigma_table_check("sig.inp(real-axis)", root / "sig.inp"),
        _file_check("case.indmfl", live),
        ("case.indmfl flag", _indmfl_flag(live) == 0, f"found={_indmfl_flag(live)} expected=0"),
        _file_check("case.indmfl.matsubara", backup),
        ("backup flag", _indmfl_flag(backup) == 1, f"found={_indmfl_flag(backup)} expected=1"),
        _file_check("indmfl.diff", root / "indmfl.diff"),
        _manifest_check(root),
    ]
    _optional_pbs(checks, root / "run_dos.pbs")
    if (root / f"{case}.cdos").exists():
        marker_ok, marker_detail = _dmft1_end(root, case)
        checks.append(("DMFT1 END", marker_ok, marker_detail))
        for rel in [f"{case}.cdos", f"{case}.gc1", f"{case}.dlt1", f"{case}.Eimp1"]:
            checks.append(_file_check(rel, root / rel))
    return checks


def doctor_band(cfg) -> list[tuple[str, bool, str]]:
    root = cfg.dmft_dir / "band"
    case = cfg.case
    live = root / f"{case}.indmfl"
    backup = root / f"{case}.indmfl.matsubara"
    klist = root / f"{case}.klist_band"
    checks = [
        _sigma_table_check("sig.inp(real-axis)", root / "sig.inp"),
        _file_check("case.klist_band", klist),
        _file_check("case.indmfl", live),
        ("case.indmfl flag", _indmfl_flag(live) == 0, f"found={_indmfl_flag(live)} expected=0"),
        _file_check("case.indmfl.matsubara", backup),
        ("backup flag", _indmfl_flag(backup) == 1, f"found={_indmfl_flag(backup)} expected=1"),
        _file_check("indmfl.diff", root / "indmfl.diff"),
        _manifest_check(root),
    ]
    _optional_pbs(checks, root / "run_band.pbs")
    if klist.exists() and klist.stat().st_size > 0:
        try:
            checks.append(("k-point count", True, str(count_klist_points(klist))))
        except WorkflowError as exc:
            checks.append(("k-point count", False, str(exc)))
    if (root / "eigvals.dat").exists():
        result = validate_band_outputs(root, case)
        checks.append((
            "band outputs",
            bool(result["ok"]),
            f"klist={result['expected']} numkpt={result['numkpt']} tot-k={result['totk']} eigvals={result['eigvals_blocks']}",
        ))
    return checks


def doctor(cfg, stage: str = "dmft") -> list[tuple[str, bool, str]]:
    if stage == "dft":
        return doctor_dft(cfg)
    if stage == "dmft":
        return doctor_dmft(cfg)
    if stage == "maxent":
        return doctor_maxent(cfg)
    if stage == "dos":
        return doctor_dos(cfg)
    if stage == "band":
        return doctor_band(cfg)
    raise WorkflowError(f"Unsupported doctor stage: {stage}")


def validate_band_outputs(band_dir: Path, case: str) -> dict:
    klist = band_dir / f"{case}.klist_band"
    expected = count_klist_points(klist)
    out = band_dir / f"{case}.outputdmfp"
    numkpt = grep_int(out, r"numkpt=\s*(\d+)")
    totk = grep_int(out, r"tot-k=(\d+)")
    eigvals = band_dir / "eigvals.dat"
    blocks = count_eigvals_blocks(eigvals) if eigvals.exists() else 0
    ok = (numkpt in (None, expected)) and (totk in (None, expected)) and blocks == expected
    return {
        "expected": expected,
        "numkpt": numkpt,
        "totk": totk,
        "eigvals_blocks": blocks,
        "ok": ok,
    }


def count_eigvals_blocks(path: Path) -> int:
    require_file(path)
    count = 0
    with path.open(errors="ignore") as f:
        while True:
            header = f.readline()
            if not header:
                break
            data = header.split()
            if len(data) < 6:
                continue
            try:
                _ikp, _isym, _nbands, _nemin, nomega = map(int, data[1:6])
            except ValueError:
                continue
            count += 1
            for _ in range(nomega):
                if not f.readline():
                    raise WorkflowError(f"Unexpected EOF in {path} while reading block {count}")
    return count
