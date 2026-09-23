from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .utils import WorkflowError
from .checks import convergence_report, doctor, format_convergence
from .maxent import run_maxent
from .realaxis import run_dos
from .band import run_band
from .plotting import plot_akw, plot_dos, plot_self_energy
from .pbs import submit_pbs, write_pbs


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edmft-workflow",
        description="Automate WIEN2k + Haule eDMFT post-processing after run_dmft.py.",
    )
    p.add_argument("-c", "--config", default="config.toml", help="Path to TOML configuration")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check required files and basic environment assumptions")
    sub.add_parser("check", help="Summarize info.iterate convergence")

    runp = sub.add_parser("run", help="Run one workflow stage in the foreground/current job")
    runp.add_argument("stage", choices=["maxent", "dos", "band", "plots", "all"])
    runp.add_argument("--force", action="store_true", help="Back up and recreate existing stage directory")

    prep = sub.add_parser("pbs", help="Write a PBS script without submitting it")
    prep.add_argument("stage", choices=["maxent", "dos", "band"])
    prep.add_argument("--force", action="store_true")

    submit = sub.add_parser("submit", help="Submit one stage or the full chain to PBS/Torque")
    submit.add_argument("stage", choices=["maxent", "dos", "band", "all"])
    submit.add_argument("--force", action="store_true")
    return p


def _run_stage(cfg, stage: str, force: bool) -> None:
    if stage == "maxent":
        run_maxent(cfg, force=force)
        plot_self_energy(cfg)
    elif stage == "dos":
        run_dos(cfg, force=force)
        plot_dos(cfg)
    elif stage == "band":
        run_band(cfg, force=force)
        plot_akw(cfg)
    elif stage == "plots":
        plot_self_energy(cfg)
        plot_dos(cfg)
        plot_akw(cfg)
    elif stage == "all":
        run_maxent(cfg, force=force)
        plot_self_energy(cfg)
        run_dos(cfg, force=force)
        plot_dos(cfg)
        run_band(cfg, force=force)
        plot_akw(cfg)


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "doctor":
            all_ok = True
            for name, ok, detail in doctor(cfg):
                print(f"{'OK' if ok else 'FAIL':4s}  {name:20s}  {detail}")
                all_ok &= ok
            return 0 if all_ok else 2

        if args.command == "check":
            report = convergence_report(
                cfg.dmft_dir,
                max_dn=float(cfg.get("convergence.max_dn", 5e-3)),
                drift_tol=float(cfg.get("convergence.outer_drift", 5e-3)),
            )
            print(format_convergence(report))
            return 0 if report["pass"] else 3

        if args.command == "run":
            _run_stage(cfg, args.stage, args.force)
            return 0

        if args.command == "pbs":
            print(write_pbs(cfg, args.stage, force=args.force))
            return 0

        if args.command == "submit":
            if args.stage != "all":
                submit_pbs(cfg, args.stage, force=args.force)
                return 0
            j1 = submit_pbs(cfg, "maxent", force=args.force)
            j2 = submit_pbs(cfg, "dos", force=args.force, depends_on=j1)
            j3 = submit_pbs(cfg, "band", force=args.force, depends_on=j2)
            print(f"chain: maxent={j1} -> dos={j2} -> band={j3}")
            return 0

        return 1
    except (ConfigError, WorkflowError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
