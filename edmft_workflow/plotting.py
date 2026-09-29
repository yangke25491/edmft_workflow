from __future__ import annotations

from pathlib import Path
import math
from collections.abc import Iterable

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .utils import WorkflowError, require_file


LABEL_MAP = {
    "GAMMA": r"$\Gamma$", "LAMBDA": r"$\Lambda$", "DELTA": r"$\Delta$",
    "W": r"$W$", "L": r"$L$", "X": r"$X$", "Z": r"$Z$", "K": r"$K$",
    "R": r"$R$", "S": r"$S$", "T": r"$T$", "U": r"$U$", "Y": r"$Y$",
}


def resolve_plot_formats(cfg, override: str | Iterable[str] | None = None) -> tuple[str, ...]:
    """Resolve requested figure formats to a normalized tuple of png/pdf.

    Accepted user-facing modes are ``png``, ``pdf``, or ``both``.  The command
    line override wins; otherwise ``plot.format`` from config is used; the
    backwards-compatible default is ``both``.
    """
    raw = override if override is not None else cfg.get("plot.format", "both")
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode == "both":
            return ("png", "pdf")
        if mode in {"png", "pdf"}:
            return (mode,)
        raise WorkflowError("plot format must be one of: png, pdf, both")

    formats = tuple(str(x).strip().lower() for x in raw)
    if not formats or any(x not in {"png", "pdf"} for x in formats):
        raise WorkflowError("plot formats must contain only png and/or pdf")
    # Preserve order while removing duplicates.
    return tuple(dict.fromkeys(formats))


def _save(fig, stem: Path, formats: str | Iterable[str] | None = None, cfg=None) -> list[Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    if cfg is None and formats is None:
        resolved = ("png", "pdf")
    elif cfg is None:
        if isinstance(formats, str):
            mode = formats.lower()
            resolved = ("png", "pdf") if mode == "both" else (mode,)
        else:
            resolved = tuple(formats)
    else:
        resolved = resolve_plot_formats(cfg, formats)

    created: list[Path] = []
    for fmt in resolved:
        path = stem.with_suffix(f".{fmt}")
        if fmt == "png":
            fig.savefig(path, dpi=300, bbox_inches="tight")
        else:
            fig.savefig(path, bbox_inches="tight")
        created.append(path)
    plt.close(fig)
    return created


def plot_dos(cfg, formats: str | Iterable[str] | None = None) -> list[Path]:
    case = cfg.case
    src = cfg.work_root / "onreal"
    out = cfg.work_root / "results"
    out.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    resolved = resolve_plot_formats(cfg, formats)

    cdos = np.loadtxt(require_file(src / f"{case}.cdos"), comments="#")
    xlim = (float(cfg.get("plot.dos_xmin", cfg.get("dos.wmin", -3.0))),
            float(cfg.get("plot.dos_xmax", cfg.get("dos.wmax", 1.0))))
    fig, ax = plt.subplots(figsize=(7, 5))
    labels = cfg.get("plot.cdos_labels", ["Total DOS", "Correlated shell"])
    for i in range(1, cdos.shape[1]):
        label = labels[i - 1] if i - 1 < len(labels) else f"DOS {i}"
        ax.plot(cdos[:, 0], cdos[:, i], label=label)
    ax.axvline(0, linestyle="--", linewidth=1)
    ax.set_xlim(*xlim)
    ax.set_xlabel(r"$\omega$ (eV)")
    ax.set_ylabel("DOS")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, out / "dos_total", resolved)

    gc = np.loadtxt(require_file(src / f"{case}.gc1"), comments="#")
    nch = (gc.shape[1] - 1) // 2
    orbital_labels = cfg.get("plot.orbital_labels", [f"orbital {i+1}" for i in range(nch)])
    fig, ax = plt.subplots(figsize=(7, 5))
    for i in range(nch):
        spectral = -gc[:, 2 + 2 * i] / np.pi
        label = orbital_labels[i] if i < len(orbital_labels) else f"orbital {i+1}"
        ax.plot(gc[:, 0], spectral, label=label)
    ax.axvline(0, linestyle="--", linewidth=1)
    ax.set_xlim(*xlim)
    ax.set_xlabel(r"$\omega$ (eV)")
    ax.set_ylabel(r"$A(\omega)=-\mathrm{Im}G/\pi$")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, out / "spectral_local", resolved)

    dlt = np.loadtxt(require_file(src / f"{case}.dlt1"), comments="#")
    nch = (dlt.shape[1] - 1) // 2
    fig, ax = plt.subplots(figsize=(7, 5))
    for i in range(nch):
        hyb = -dlt[:, 2 + 2 * i]
        label = orbital_labels[i] if i < len(orbital_labels) else f"orbital {i+1}"
        ax.plot(dlt[:, 0], hyb, label=label)
    ax.axvline(0, linestyle="--", linewidth=1)
    ax.set_xlim(*xlim)
    ax.set_xlabel(r"$\omega$ (eV)")
    ax.set_ylabel(r"$-\mathrm{Im}\Delta(\omega)$")
    ax.legend()
    fig.tight_layout()
    created += _save(fig, out / "hybridization", resolved)
    return created


def plot_self_energy(cfg, formats: str | Iterable[str] | None = None) -> list[Path]:
    out = cfg.work_root / "results"
    out.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    labels = cfg.get("plot.orbital_labels", [])
    resolved = resolve_plot_formats(cfg, formats)
    for source, name, xlabel in [
        (cfg.work_root / "maxent" / "sig.inpx", "sigma_matsubara", r"$\omega_n$ (eV)"),
        (cfg.work_root / "maxent" / "Sig.out", "sigma_realaxis", r"$\omega$ (eV)"),
    ]:
        if not source.exists():
            continue
        data = np.loadtxt(source, comments="#")
        nch = (data.shape[1] - 1) // 2
        fig, ax = plt.subplots(figsize=(7, 5))
        for i in range(nch):
            y = -data[:, 2 + 2 * i]
            label = labels[i] if i < len(labels) else f"orbital {i+1}"
            ax.plot(data[:, 0], y, label=label)
        ax.axvline(0, linestyle="--", linewidth=1)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(r"$-\mathrm{Im}\Sigma$")
        ax.legend()
        fig.tight_layout()
        created += _save(fig, out / name, resolved)
    return created


def read_klabels(path: Path, drop_labels: list[str] | None = None) -> tuple[list[int], list[str], int]:
    drop = {x.upper() for x in (drop_labels or [])}
    idx: list[int] = []
    labels: list[str] = []
    n = 0
    with path.open(errors="ignore") as f:
        for il, line in enumerate(f):
            if line[:3].strip().upper() == "END" or line.strip() == "END":
                break
            n += 1
            token = line[:10].split()
            if token:
                raw = token[0].upper()
                if raw not in drop:
                    idx.append(il)
                    labels.append(LABEL_MAP.get(raw, f"${raw}$"))
    if not idx:
        raise WorkflowError(f"No labeled k points found in {path}")
    return idx, labels, n


def read_eigvals(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return omega[nomega] and complex eigenvalues[nk,nomega,nbands]."""
    require_file(path)
    blocks: list[np.ndarray] = []
    omega_ref = None
    nbands_ref = None
    with path.open(errors="ignore") as f:
        while True:
            header = f.readline()
            if not header:
                break
            fields = header.split()
            if len(fields) < 6:
                continue
            try:
                _ikp, _isym, nbands, _nemin, nomega = map(int, fields[1:6])
            except ValueError:
                continue
            om = np.empty(nomega, float)
            eig = np.empty((nomega, nbands), complex)
            for iom in range(nomega):
                line = f.readline()
                if not line:
                    raise WorkflowError("Unexpected EOF in eigvals.dat")
                vals = np.asarray([float(x) for x in line.split()], float)
                if vals.size != 1 + 2 * nbands:
                    raise WorkflowError(
                        f"Malformed eigvals row: expected {1+2*nbands} columns, got {vals.size}"
                    )
                om[iom] = vals[0]
                eig[iom] = vals[1::2] + 1j * vals[2::2]
            if omega_ref is None:
                omega_ref = om
                nbands_ref = nbands
            else:
                if nbands != nbands_ref or not np.allclose(om, omega_ref):
                    raise WorkflowError("Inconsistent eigvals.dat blocks")
            blocks.append(eig)
    if not blocks or omega_ref is None:
        raise WorkflowError(f"No eigvals blocks parsed from {path}")
    return omega_ref, np.stack(blocks, axis=0)


def akw_from_eigvals(omega: np.ndarray, eig: np.ndarray, mu: float, small: float = 1e-5) -> np.ndarray:
    """Pure NumPy equivalent of Haule eDMFT cakw.Akw with unit coherence factors."""
    nk, nw, nb = eig.shape
    out = np.empty((nw, nk), float)
    for ik in range(nk):
        ek = eig[ik].copy()
        imag = ek.imag
        mask = imag > -small
        if np.any(mask):
            ek.imag[mask] = -small
        denom = omega[:, None] + mu - ek
        out[:, ik] = np.sum(-np.imag(1.0 / denom) / np.pi, axis=1)
    return out


def _hist_cutoff(a: np.ndarray, intensity: float) -> float:
    intensity = min(max(float(intensity), 1e-6), 0.999999)
    hist, edges = np.histogram(a.ravel(), bins=5000)
    centers = 0.5 * (edges[1:] + edges[:-1])
    c = np.cumsum(hist) / np.sum(hist)
    i = min(np.searchsorted(c, intensity), len(centers) - 1)
    return float(centers[i])


def plot_akw(cfg, formats: str | Iterable[str] | None = None) -> list[Path]:
    band = cfg.work_root / "band"
    case = cfg.case
    require_file(band / "eigvals.dat")
    require_file(band / "EF.dat")
    if (band / "cohfactorsd.dat").exists():
        raise WorkflowError(
            "cohfactorsd.dat is present. v0.1 pure-NumPy plotter currently supports unit coherence factors only; "
            "use official wakplot.py for this case."
        )
    mu = float((band / "EF.dat").read_text().strip())
    omega, eig = read_eigvals(band / "eigvals.dat")
    drop = cfg.get("plot.drop_klabels", [])
    tick_idx, tick_labels, nk_klist = read_klabels(band / f"{case}.klist_band", drop_labels=drop)
    if eig.shape[0] != nk_klist:
        raise WorkflowError(f"eigvals has {eig.shape[0]} k blocks but klist_band has {nk_klist} points")
    akw = akw_from_eigvals(omega, eig, mu=mu, small=float(cfg.get("plot.akw_small", 1e-5)))
    intensity = float(cfg.get("plot.akw_intensity", 0.97))
    vmax = _hist_cutoff(akw, intensity)
    if not math.isfinite(vmax) or vmax <= 0:
        raise WorkflowError("A(k,w) intensity cutoff is non-positive; inspect eigvals.dat and EF.dat")

    out = cfg.work_root / "results"
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "akw_data.npz", omega=omega, akw=akw, mu=mu)

    fig, ax = plt.subplots(figsize=(8, 6))
    extent = [0, eig.shape[0] - 1, omega[0], omega[-1]]
    ax.imshow(akw, interpolation="bilinear", cmap="hot", origin="lower",
              vmin=0, vmax=vmax, extent=extent, aspect="auto")
    for x in tick_idx:
        ax.axvline(x, linewidth=0.8)
    ax.axhline(0, linestyle="--", linewidth=0.8)
    ax.set_xticks(tick_idx, tick_labels)
    ax.set_ylabel(r"$\omega$ (eV)")
    ax.set_xlim(0, eig.shape[0] - 1)
    if cfg.get("plot.akw_ymin") is not None or cfg.get("plot.akw_ymax") is not None:
        ymin = float(cfg.get("plot.akw_ymin", omega[0]))
        ymax = float(cfg.get("plot.akw_ymax", omega[-1]))
        ax.set_ylim(ymin, ymax)
    fig.tight_layout()
    created = _save(fig, out / "Akw", resolve_plot_formats(cfg, formats))
    print(f"A(k,w): nk={eig.shape[0]}, nomega={len(omega)}, mu={mu:.9f}, cutoff={vmax:.6g}")
    return created
