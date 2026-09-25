from __future__ import annotations

import argparse
import sys

from .analysis import run_analysis
from .config import ConfigError, load_config
from .utils import WorkflowError
from .checks import convergence_report, doctor, format_convergence
from .environment import print_environment_report
from .production import init_layout, prepare_dmft
from .maxent import prepare_maxent
from .realaxis import prepare_dos
from .band import prepare_band
from .pbs import write_pbs
from .foreground import run_foreground


PLOT_FORMATS = ("png", "pdf", "both")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edmft-workflow",
        description=(
            "Transparent WIEN2k + Haule eDMFT workflow manager: prepare files, generate standalone PBS for "
            "DFT/DMFT/MaxEnt, run DOS/band in foreground MPI, check results, and analyze outputs."
        ),
    )
    p.add_argument("-c", "--config", default="config.toml", help="Path to TOML configuration")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-layout", help="Create project directories; does not run scientific initializers")

    prepdmft = sub.add_parser(
        "prepare-dmft",
        help="Create isolated dmft/ snapshot after converged DFT + manual init_dmft.py in dft/",
    )
    prepdmft.add_argument("--force", action="store_true")

    for stage in ("maxent", "dos", "band"):
        sp = sub.add_parser(
            f"prepare-{stage}",
            help=f"Prepare {stage} native inputs only; inspect them before numerical execution",
        )
        sp.add_argument("--force", action="store_true")

    sub.add_parser("doctor-env", help="Compatibility alias: check runtime environment")
    doc = sub.add_parser("doctor", help="Check a prepared calculation stage")
    doc.add_argument(
        "stage",
        nargs="?",
        choices=["dft", "dmft", "maxent", "dos", "band", "env"],
        default="dmft",
    )

    chk = sub.add_parser("check", help="Check scientific convergence/results")
    chk.add_argument("stage", nargs="?", choices=["dmft"], default="dmft")

    sub.add_parser("status", help="Show which workflow stages are prepared/completed")

    prep = sub.add_parser(
        "pbs",
        help="Freeze DFT/DMFT/MaxEnt inputs into a standalone PBS script; qsub is always manual",
    )
    prep.add_argument("stage", choices=["dft", "dmft", "maxent"])
    prep.add_argument("--force", action="store_true")

    runp = sub.add_parser("run", help="Foreground operations")
    runp.add_argument("stage", choices=["dos", "band", "plots"])
    runp.add_argument(
        "--format",
        choices=PLOT_FORMATS,
        default=None,
        help="Figure format for 'run plots': png, pdf, or both; overrides plot.format",
    )

    analyze = sub.add_parser("analyze", help="Generate common scientific diagnostics in the foreground")
    analyze.add_argument("stage", nargs="?", choices=["all"], default="all")
    analyze.add_argument(
        "--format",
        choices=PLOT_FORMATS,
        default=None,
        help="Figure format: png, pdf, or both; overrides plot.format for this run",
    )
    return p


def _print_checks(rows) -> bool:
    all_ok = True
    for name, ok, detail in rows:
        print(f"{'OK' if ok else 'FAIL':4s}  {name:26s}  {detail}")
        all_ok &= ok
    return all_ok


def _status(cfg) -> None:
    case = cfg.case
    stages = [
        ("DFT result", cfg.dft_dir / f"{case}.scf"),
        ("DFT init_dmft", cfg.dft_dir / f"{case}.indmfi"),
        ("DFT PBS", cfg.dft_dir / "run_dft.pbs"),
        ("DMFT result", cfg.dmft_dir / "info.iterate"),
        ("DMFT PBS", cfg.dmft_dir / "run_dmft.pbs"),
        ("MaxEnt inputs", cfg.dmft_dir / "maxent" / "manifest.json"),
        ("MaxEnt PBS", cfg.dmft_dir / "maxent" / "run_maxent.pbs"),
        ("MaxEnt result", cfg.dmft_dir / "maxent" / "Sig.out"),
        ("DOS inputs", cfg.dmft_dir / "onreal" / "manifest.json"),
        ("DOS result", cfg.dmft_dir / "onreal" / f"{case}.cdos"),
        ("Band inputs", cfg.dmft_dir / "band" / "manifest.json"),
        ("Band result", cfg.dmft_dir / "band" / "eigvals.dat"),
        ("Analysis", cfg.dmft_dir / "analysis" / "summary.md"),
    ]
    for label, path in stages:
        ok = path.exists() and path.is_file() and path.stat().st_size > 0
        print(f"{'DONE' if ok else '--':4s}  {label:18s}  {path}")


def _prepare_stage(cfg, stage: str, force: bool) -> None:
    if stage == "maxent":
        out = prepare_maxent(cfg, force=force)
    elif stage == "dos":
        out = prepare_dos(cfg, force=force)
    elif stage == "band":
        out = prepare_band(cfg, force=force)
    else:  # pragma: no cover
        raise WorkflowError(f"Unsupported prepare stage: {stage}")

    print(f"Prepared native {stage} inputs: {out}")
    print(f"Next check: python workflow.py -c {cfg.source} doctor {stage}")
    if stage == "maxent":
        print(f"After inspection/editing, freeze PBS: python workflow.py -c {cfg.source} pbs maxent")
        print("Then inspect run_maxent.pbs and submit it yourself with qsub.")
    else:
        print(f"After inspection, run foreground MPI: python workflow.py -c {cfg.source} run {stage}")


def _analyze(cfg, plot_format: str | None = None) -> None:
    created = run_analysis(cfg, plot_format=plot_format)
    print("Analysis outputs:")
    for p in created:
        print(f"  {p}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)

        if args.command == "init-layout":
            init_layout(cfg)
            return 0

        if args.command == "prepare-dmft":
            prepare_dmft(cfg, force=args.force)
            return 0

        if args.command in {"prepare-maxent", "prepare-dos", "prepare-band"}:
            stage = args.command.removeprefix("prepare-")
            _prepare_stage(cfg, stage, args.force)
            return 0

        if args.command == "doctor-env":
            return 0 if print_environment_report(cfg) else 2

        if args.command == "doctor":
            if args.stage == "env":
                return 0 if print_environment_report(cfg) else 2
            return 0 if _print_checks(doctor(cfg, args.stage)) else 2

        if args.command == "check":
            report = convergence_report(
                cfg.dmft_dir,
                max_dn=float(cfg.get("convergence.max_dn", 5e-3)),
                drift_tol=float(cfg.get("convergence.outer_drift", 5e-3)),
            )
            print(format_convergence(report))
            return 0 if report["pass"] else 3

        if args.command == "status":
            _status(cfg)
            return 0

        if args.command == "pbs":
            print(write_pbs(cfg, args.stage, force=args.force))
            return 0

        if args.command == "run":
            if args.stage in {"dos", "band"}:
                run_foreground(cfg, args.stage)
                return 0
            if args.stage == "plots":
                _analyze(cfg, plot_format=args.format)
                return 0

        if args.command == "analyze":
            _analyze(cfg, plot_format=args.format)
            return 0

        return 1
    except (ConfigError, WorkflowError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
