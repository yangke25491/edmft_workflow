from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .utils import WorkflowError
from .checks import convergence_report, doctor, format_convergence
from .environment import print_environment_report
from .production import init_layout, prepare_dmft, run_dft, run_dmft
from .maxent import run_maxent
from .realaxis import run_dos
from .band import run_band
from .plotting import plot_akw, plot_dos, plot_self_energy
from .pbs import submit_pbs, write_pbs


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edmft-workflow",
        description=(
            "Automate WIEN2k + Haule eDMFT around two manual checkpoints, both in dft/: "
            "init_lapw before DFT and init_dmft.py after DFT convergence."
        ),
    )
    p.add_argument("-c", "--config", default="config.toml", help="Path to TOML configuration")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-layout", help="Create root/dft, root/dmft and DFT scratch; does not run initializers")

    prepdmft = sub.add_parser(
        "prepare-dmft",
        help=(
            "After converged DFT + manual init_dmft.py in dft/, create an isolated dmft/ snapshot, "
            "generate params.dat and initial sig.inp"
        ),
    )
    prepdmft.add_argument("--force", action="store_true")

    sub.add_parser("doctor-env", help="Check Intel MPI, mpi4py, MKL and eDMFT shared-library runtime")
    sub.add_parser("doctor", help="Check required DMFT files and basic workflow assumptions")
    sub.add_parser("check", help="Summarize info.iterate convergence")

    runp = sub.add_parser(
        "run",
        help="Run a stage in the current shell/job. Production compute stages are normally submitted through PBS.",
    )
    runp.add_argument("stage", choices=["dft", "dmft", "maxent", "dos", "band", "plots", "post", "all"])
    runp.add_argument("--force", action="store_true", help="Back up/recreate stage directories when supported")

    prep = sub.add_parser("pbs", help="Write a PBS compute script without submitting it")
    prep.add_argument("stage", choices=["dft", "dmft", "maxent", "dos", "band"])
    prep.add_argument("--force", action="store_true")

    submit = sub.add_parser("submit", help="Submit a compute stage or the post-processing compute chain to PBS/Torque")
    submit.add_argument("stage", choices=["dft", "dmft", "maxent", "dos", "band", "post"])
    submit.add_argument("--force", action="store_true")
    return p


def _run_stage(cfg, stage: str, force: bool) -> None:
    if stage == "dft":
        run_dft(cfg)
    elif stage == "dmft":
        run_dmft(cfg)
    elif stage == "maxent":
        run_maxent(cfg, force=force)
    elif stage == "dos":
        run_dos(cfg, force=force)
    elif stage == "band":
        run_band(cfg, force=force)
    elif stage == "plots":
        plot_self_energy(cfg)
        plot_dos(cfg)
        plot_akw(cfg)
    elif stage == "post":
        run_maxent(cfg, force=force)
        run_dos(cfg, force=force)
        run_band(cfg, force=force)
    elif stage == "all":
        raise WorkflowError(
            "'run all' is intentionally disabled because init_lapw and init_dmft.py are manual checkpoints. "
            "Use: init_lapw in dft/ -> submit dft -> init_dmft.py in dft/ -> prepare-dmft -> "
            "submit dmft -> submit post."
        )


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

        if args.command == "doctor-env":
            return 0 if print_environment_report(cfg) else 2

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
            if args.stage != "post":
                submit_pbs(cfg, args.stage, force=args.force)
                return 0
            j1 = submit_pbs(cfg, "maxent", force=args.force)
            j2 = submit_pbs(cfg, "dos", force=args.force, depends_on=j1)
            j3 = submit_pbs(cfg, "band", force=args.force, depends_on=j2)
            print(f"post compute chain: maxent={j1} -> dos={j2} -> band={j3}")
            print("After the band job finishes, run `workflow.py -c config.toml run plots` in the foreground.")
            return 0

        return 1
    except (ConfigError, WorkflowError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
