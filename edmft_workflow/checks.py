from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Iterable

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


def doctor(cfg) -> list[tuple[str, bool, str]]:
    dmft = cfg.dmft_dir
    case = cfg.case
    checks: list[tuple[str, bool, str]] = []
    for rel in [
        f"{case}.struct", f"{case}.indmfl", "params.dat", "projectorw.dat",
        "info.iterate",
    ]:
        p = dmft / rel
        ok = p.exists() and p.stat().st_size > 0
        checks.append((rel, ok, str(p)))
    sigs = sorted(dmft.glob("sig.inp.*.1"))
    checks.append(("sig.inp.*.1", bool(sigs), f"{len(sigs)} files"))
    imp = dmft / "imp.0"
    checks.append(("imp.0/", imp.is_dir(), str(imp)))
    return checks


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
