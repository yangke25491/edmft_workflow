from __future__ import annotations

from pathlib import Path
import csv
import math
import re
from collections.abc import Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .checks import convergence_report, last_of_each_outer, parse_info_iterate
from .plotting import plot_akw, plot_dos, resolve_plot_formats
from .utils import WorkflowError, require_file


def _save(fig, stem: Path, formats: Iterable[str]) -> list[Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for fmt in formats:
        path = stem.with_suffix(f".{fmt}")
        if fmt == "png":
            fig.savefig(path, dpi=300, bbox_inches="tight")
        else:
            fig.savefig(path, bbox_inches="tight")
        created.append(path)
    plt.close(fig)
    return created


def _load_sigma(path: Path) -> np.ndarray:
    require_file(path)
    try:
        data = np.loadtxt(path, comments="#")
    except ValueError as exc:
        raise WorkflowError(f"Could not parse self-energy file {path}: {exc}") from exc
    if data.ndim != 2 or data.shape[1] < 3 or (data.shape[1] - 1) % 2:
        raise WorkflowError(
            f"Unexpected self-energy table in {path}: shape={data.shape}; expected omega + Re/Im pairs"
        )
    if not np.all(np.isfinite(data)):
        raise WorkflowError(f"Self-energy contains NaN/Inf: {path}")
    return data


def _latest_sigma(dmft_dir: Path, impurity: int) -> Path:
    found: list[tuple[int, Path]] = []
    rgx = re.compile(rf"sig\.inp\.(\d+)\.{impurity}$")
    for path in dmft_dir.glob(f"sig.inp.*.{impurity}"):
        m = rgx.fullmatch(path.name)
        if m and path.is_file() and path.stat().st_size > 0:
            found.append((int(m.group(1)), path))
    if not found:
        raise WorkflowError(f"No sig.inp.*.{impurity} files found in {dmft_dir}")
    found.sort(key=lambda x: x[0])
    return found[-1][1]


def analyze_convergence(cfg, formats: Iterable[str]) -> tuple[list[Path], dict]:
    """Export convergence history and simple diagnostic plots."""
    root = cfg.dmft_dir / "analysis" / "convergence"
    root.mkdir(parents=True, exist_ok=True)
    rows = last_of_each_outer(parse_info_iterate(cfg.dmft_dir / "info.iterate"))
    report = convergence_report(
        cfg.dmft_dir,
        max_dn=float(cfg.get("convergence.max_dn", 5e-3)),
        drift_tol=float(cfg.get("convergence.outer_drift", 5e-3)),
    )

    csv_path = root / "info_iterate_outer.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["outer", "inner", "mu_eV", "Vdc_eV", "n_latt", "n_imp", "abs_dn"])
        for r in rows:
            w.writerow([r.outer, r.inner, r.mu, r.vdc, r.n_latt, r.n_imp, r.dn])

    x = np.asarray([r.outer for r in rows], float)
    n_latt = np.asarray([r.n_latt for r in rows], float)
    n_imp = np.asarray([r.n_imp for r in rows], float)
    dn = np.abs(n_latt - n_imp)
    mu = np.asarray([r.mu for r in rows], float)
    vdc = np.asarray([r.vdc for r in rows], float)

    created = [csv_path]

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(x, n_latt, marker="o", label="n_latt")
    ax.plot(x, n_imp, marker="o", label="n_imp")
    ax.set_xlabel("outer DMFT cycle")
    ax.set_ylabel("occupancy")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, root / "occupancy", formats)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(x, dn, marker="o")
    ax.axhline(float(report["max_dn"]), linestyle="--", linewidth=1)
    ax.set_xlabel("outer DMFT cycle")
    ax.set_ylabel(r"$|n_{latt}-n_{imp}|$")
    fig.tight_layout()
    created += _save(fig, root / "occupancy_difference", formats)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(x, mu, marker="o")
    ax.set_xlabel("outer DMFT cycle")
    ax.set_ylabel(r"$\mu$ (eV)")
    fig.tight_layout()
    created += _save(fig, root / "chemical_potential", formats)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(x, vdc, marker="o")
    ax.set_xlabel("outer DMFT cycle")
    ax.set_ylabel(r"$V_{dc}$ (eV)")
    fig.tight_layout()
    created += _save(fig, root / "double_counting", formats)

    return created, report


def _fit_z(
    omega: np.ndarray,
    imag_sigma: np.ndarray,
    nfit: int,
) -> tuple[float, float, float, float, float]:
    if nfit < 2:
        raise WorkflowError("analysis.z_fit_points must be >= 2")
    n = min(nfit, len(omega))
    x = np.asarray(omega[:n], float)
    y = np.asarray(imag_sigma[:n], float)
    if len(x) < 2:
        return math.nan, math.nan, math.nan, math.nan, math.nan
    slope, intercept = np.polyfit(x, y, 1)
    yfit = slope * x + intercept
    ss_res = float(np.sum((y - yfit) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    denom = 1.0 - slope
    z = 1.0 / denom if abs(denom) > 1e-14 else math.nan
    mass = 1.0 / z if math.isfinite(z) and abs(z) > 1e-14 else math.nan
    return float(slope), float(intercept), float(r2), float(z), float(mass)


def analyze_self_energy(cfg, formats: Iterable[str]) -> tuple[list[Path], list[dict], Path]:
    """Plot Matsubara/real-axis self-energy and export low-frequency Z diagnostics."""
    root = cfg.dmft_dir / "analysis" / "self_energy"
    root.mkdir(parents=True, exist_ok=True)
    impurity = int(cfg.get("post.impurity", 1))
    averaged = cfg.dmft_dir / "maxent" / "sig.inpx"
    source = averaged if averaged.exists() and averaged.stat().st_size > 0 else _latest_sigma(cfg.dmft_dir, impurity)
    data = _load_sigma(source)
    omega = data[:, 0]
    nch = (data.shape[1] - 1) // 2
    labels = cfg.get("plot.orbital_labels", [])
    nfit = int(cfg.get("analysis.z_fit_points", 4))

    created: list[Path] = []

    fig, ax = plt.subplots(figsize=(7, 5))
    for i in range(nch):
        label = labels[i] if i < len(labels) else f"channel {i+1}"
        ax.plot(omega, data[:, 1 + 2 * i], label=label)
    ax.set_xlabel(r"$\omega_n$ (eV)")
    ax.set_ylabel(r"Re $\Sigma(i\omega_n)$ (eV)")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, root / "sigma_matsubara_real", formats)

    fig, ax = plt.subplots(figsize=(7, 5))
    for i in range(nch):
        label = labels[i] if i < len(labels) else f"channel {i+1}"
        ax.plot(omega, -data[:, 2 + 2 * i], label=label)
    ax.set_xlabel(r"$\omega_n$ (eV)")
    ax.set_ylabel(r"-Im $\Sigma(i\omega_n)$ (eV)")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, root / "sigma_matsubara_imag", formats)

    zrows: list[dict] = []
    for i in range(nch):
        slope, intercept, r2, z, mass = _fit_z(omega, data[:, 2 + 2 * i], nfit)
        zrows.append({
            "channel": i + 1,
            "label": labels[i] if i < len(labels) else f"channel {i+1}",
            "fit_points": min(nfit, len(omega)),
            "slope_dImSigma_domega": slope,
            "intercept_eV": intercept,
            "R2": r2,
            "Z": z,
            "mass_enhancement_1_over_Z": mass,
        })

    zcsv = root / "z_mass_diagnostic.csv"
    with zcsv.open("w", newline="", encoding="utf-8") as f:
        fields = list(zrows[0].keys()) if zrows else ["channel"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(zrows)
    created.append(zcsv)

    sigout = cfg.dmft_dir / "maxent" / "Sig.out"
    if sigout.exists() and sigout.stat().st_size > 0:
        real = _load_sigma(sigout)
        wr = real[:, 0]
        nr = (real.shape[1] - 1) // 2

        fig, ax = plt.subplots(figsize=(7, 5))
        for i in range(nr):
            label = labels[i] if i < len(labels) else f"channel {i+1}"
            ax.plot(wr, real[:, 1 + 2 * i], label=label)
        ax.axvline(0, linestyle="--", linewidth=1)
        ax.set_xlabel(r"$\omega$ (eV)")
        ax.set_ylabel(r"Re $\Sigma(\omega)$ (eV)")
        ax.legend()
        fig.tight_layout()
        created += _save(fig, root / "sigma_realaxis_real", formats)

        fig, ax = plt.subplots(figsize=(7, 5))
        for i in range(nr):
            label = labels[i] if i < len(labels) else f"channel {i+1}"
            ax.plot(wr, -real[:, 2 + 2 * i], label=label)
        ax.axvline(0, linestyle="--", linewidth=1)
        ax.set_xlabel(r"$\omega$ (eV)")
        ax.set_ylabel(r"-Im $\Sigma(\omega)$ (eV)")
        ax.legend()
        fig.tight_layout()
        created += _save(fig, root / "sigma_realaxis_imag", formats)

    return created, zrows, source


def _fmt(value: float) -> str:
    return f"{value:.8g}" if math.isfinite(value) else "nan"


def write_summary(cfg, report: dict, zrows: list[dict], sigma_source: Path, extra_outputs: list[Path]) -> Path:
    out = cfg.dmft_dir / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    last = report["last"]
    lines = [
        "# DFT+DMFT analysis summary",
        "",
        f"- case: `{cfg.case}`",
        f"- Matsubara self-energy analyzed: `{sigma_source}`",
        f"- last outer cycle: {last.outer}",
        f"- last charge iteration: {last.inner}",
        f"- mu: {_fmt(last.mu)} eV",
        f"- Vdc: {_fmt(last.vdc)} eV",
        f"- n_latt: {_fmt(last.n_latt)}",
        f"- n_imp: {_fmt(last.n_imp)}",
        f"- |n_latt-n_imp|: {_fmt(last.dn)}",
        f"- convergence status: {'PASS' if report['pass'] else 'WARNING'}",
        "",
        "## Low-frequency self-energy diagnostic",
        "",
        "The Z values below use a linear fit to the first configured Matsubara points. "
        "They are diagnostics, not a substitute for checking whether the low-frequency self-energy is actually linear.",
        "",
        "| channel | label | fit points | slope dImSigma/domega | R2 | Z | 1/Z |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in zrows:
        lines.append(
            f"| {row['channel']} | {row['label']} | {row['fit_points']} | "
            f"{_fmt(row['slope_dImSigma_domega'])} | {_fmt(row['R2'])} | "
            f"{_fmt(row['Z'])} | {_fmt(row['mass_enhancement_1_over_Z'])} |"
        )
    lines += ["", "## Generated outputs", ""]
    for path in extra_outputs:
        lines.append(f"- `{path}`")
    summary = out / "summary.md"
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run_analysis(cfg, plot_format: str | None = None) -> list[Path]:
    """Run read-only/common analysis in the foreground.

    ``plot_format`` accepts png/pdf/both and overrides ``plot.format`` from the
    project config for this invocation only.
    """
    formats = resolve_plot_formats(cfg, plot_format)
    created, report = analyze_convergence(cfg, formats)
    sigma_outputs, zrows, sigma_source = analyze_self_energy(cfg, formats)
    created += sigma_outputs

    # Existing plotting helpers provide DOS/local spectra and A(k,w). They are
    # only called when their upstream outputs are present.
    onreal = cfg.dmft_dir / "onreal"
    if (onreal / f"{cfg.case}.cdos").exists():
        created += plot_dos(cfg, formats)
    if (cfg.dmft_dir / "band" / "eigvals.dat").exists():
        created += plot_akw(cfg, formats)

    summary = write_summary(cfg, report, zrows, sigma_source, created)
    created.append(summary)
    return created
