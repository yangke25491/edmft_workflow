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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edmft-workflow",
        description=(
            "Transparent WIEN2k + Haule eDMFT workflow manager: prepare files, generate standalone PBS, "
            "check results, and analyze outputs. qsub is always manual."
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
            help=f"Prepare {stage} inputs and write a standalone run_{stage}.pbs; does not qsub",
        )
        sp.add_argument("--force", action="store_true")

    sub.add_parser("doctor-env", help="Compatibility alias: check runtime environment")
    doc = sub.add_parser("doctor", help="Check a prepared calculation stage")
    doc.add_argument("stage", nargs="?", choices=["dmft", "maxent", "dos", "band", "env"], default="dmft")

    chk = sub.add_parser("check", help="Check scientific convergence/results")
    chk.add_argument("stage", nargs="?", choices=["dmft"], default="dmft")

    sub.add_parser("status", help="Show which workflow stages are prepared/completed")

    prep = sub.add_parser("pbs", help="Write a standalone PBS script; inspect it and qsub manually")
    prep.add_argument("stage", choices=["dft", "dmft", "maxent", "dos", "band"])
    prep.add_argument("--force", action="store_true")

    runp = sub.add_parser("run", help="Foreground-only lightweight operations")
    runp.add_argument("stage", choices=["plots"])

    analyze = sub.add_parser("analyze", help="Generate common scientific diagnostics in the foreground")
    analyze.add_argument("stage", nargs="?", choices=["all"], default="all")
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
        ("DFT", cfg.dft_dir / f"{case}.scf"),
        ("DMFT", cfg.dmft_dir / "info.iterate"),
        ("MaxEnt prepared", cfg.dmft_dir / "maxent" / "run_maxent.pbs"),
        ("MaxEnt complete", cfg.dmft_dir / "maxent" / "Sig.out"),
        ("DOS prepared", cfg.dmft_dir / "onreal" / "run_dos.pbs"),
        ("DOS complete", cfg.dmft_dir / "onreal" / f"{case}.cdos"),
        ("Band prepared", cfg.dmft_dir / "band" / "run_band.pbs"),
        ("Band complete", cfg.dmft_dir / "band" / "eigvals.dat"),
        ("Analysis", cfg.dmft_dir / "analysis" / "summary.md"),
    ]
    for label, path in stages:
        if path.is_dir():
            ok = path.exists() and any(path.iterdir())
        else:
            ok = path.exists() and path.stat().st_size > 0 if path.exists() else False
        print(f"{'DONE' if ok else '--':4s}  {label:18s}  {path}")


def _prepare_with_pbs(cfg, stage: str, force: bool) -> None:
    if stage == "maxent":
        prepare_maxent(cfg, force=force)
    elif stage == "dos":
        prepare_dos(cfg, force=force)
    elif stage == "band":
        prepare_band(cfg, force=force)
    else:  # pragma: no cover
        raise WorkflowError(f"Unsupported prepare stage: {stage}")
    # The freshly prepared stage directory has no PBS script, so no overwrite is needed.
    path = write_pbs(cfg, stage, force=False)
    print(f"Prepared {stage}. Review inputs and PBS before submitting:")
    print(f"  cat {path}")
    print(f"  qsub {path}")


def _analyze(cfg) -> None:
    created = run_analysis(cfg)
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
            _prepare_with_pbs(cfg, stage, args.force)
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

        if args.command == "run" and args.stage == "plots":
            _analyze(cfg)
            return 0

        if args.command == "analyze":
            _analyze(cfg)
            return 0

        return 1
    except (ConfigError, WorkflowError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
