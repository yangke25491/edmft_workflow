# edmft_workflow

A transparent workflow manager for **WIEN2k + Kristjan Haule eDMFT** on PBS/Torque clusters.

The project deliberately avoids hiding official commands behind long Python call chains. It prepares stage directories, checks files/results, writes or copies native eDMFT/WIEN2k inputs, generates **standalone PBS scripts**, and performs common read-only analysis. The user inspects each stage and runs `qsub` manually.

The two scientific initializers remain manual and both run in the complete WIEN2k DFT directory:

```text
PROJECT/dft : init_lapw      # before DFT
PROJECT/dft : init_dmft.py   # after DFT convergence
```

## Directory layout

```text
PROJECT/
├── config.toml
├── inputs/                   # optional native scientific inputs
│   ├── params.dat
│   ├── maxent_params.dat
│   └── CASE.klist_band
├── dft/
│   └── tmp/
└── dmft/
    ├── tmp/
    ├── maxent/
    │   └── manifest.json
    ├── onreal/
    │   └── manifest.json
    ├── band/
    │   └── manifest.json
    └── analysis/
```

No numerical stage uses symlinks back into an upstream calculation directory. Mutable WIEN2k/eDMFT state is copied so that `dft/`, `dmft/`, `maxent/`, `onreal/`, and `band/` stay independent.

## Zero-interference environment policy

The workflow does **not** manage or modify the user's login shell. It does not write `.bashrc`, require `env.sh`, install aliases, or permanently export `PATH`, `LD_LIBRARY_PATH`, Intel/MKL, MPI, or WIEN2k variables.

Preparation and numerical execution deliberately use different environment policies:

```text
prepare-* / doctor / check / status / analyze
    ↓
Python file operations + a few lightweight official helpers
    ↓
no Intel compilervars, no MKL/MPI setup, no PATH rewrite

pbs STAGE
    ↓
generate a standalone heavy-job script
    ↓
Intel + MKL + MPI + WIEN2k + eDMFT runtime lives inside that PBS file
    ↓
user inspects it and runs qsub manually
```

When preparation needs an upstream Python helper such as `dmft_copy.py`, `szero.py`, or `saverage.py`, the workflow invokes the configured Python interpreter and the absolute eDMFT script path directly. Only minimal child-process variables such as `WIENROOT`, `WIEN_DMFT_ROOT`, and an explicitly needed `SCRATCH` are supplied. These variables exist only in that child process; the parent terminal is unchanged.

## Run directly from git

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml <command>
```

No pip installation or workflow-specific shell environment is required.

## Native scientific inputs take priority

For reproducibility and transparency, the preferred inputs are:

```text
inputs/params.dat
inputs/maxent_params.dat
inputs/CASE.klist_band
```

If `inputs/params.dat` is absent, `prepare-dmft` can still generate it from `[dmft_params]` and `[impurityN]` as a fallback.

If `inputs/maxent_params.dat` is absent, `prepare-maxent` writes the current upstream `maxent_run.py` default template into `dmft/maxent/maxent_params.dat` so it can be inspected and edited explicitly. MaxEnt parameters are not hidden in `config.toml`.

## Core operating rule

Preparation, inspection, PBS generation, and submission are separate steps:

```text
prepare stage
    ↓
doctor / inspect native inputs
    ↓
pbs stage
    ↓
inspect standalone run_*.pbs
    ↓
qsub run_*.pbs   # always manual
```

The `pbs` command freezes the current prepared files into the provenance manifest. If a scientific input is edited after PBS generation, `doctor` will detect the manifest mismatch; regenerate the PBS with `--force` to refresh the freeze-point snapshot.

## Production workflow

Create layout:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml init-layout
```

Manual DFT initialization:

```bash
cd dft
export SCRATCH="$PWD/tmp"
init_lapw
```

Generate a standalone DFT PBS file:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dft
cat dft/run_dft.pbs
qsub dft/run_dft.pbs
```

After DFT convergence, run the second manual checkpoint in the same DFT directory:

```bash
cd dft
init_dmft.py
```

Useful check:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dft
```

Then prepare the isolated DMFT snapshot:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

`prepare-dmft` validates converged DFT + `indmf/indmfl/indmfi`, invokes official `dmft_copy.py SOURCE` from inside `dmft/`, supplements potential files omitted by upstream `dmft_copy.py`, prepares `params.dat`, invokes official `szero.py` for the initial `sig.inp`, and performs a final READY gate. These helper invocations use the lightweight child-process policy; the full Intel/MKL/MPI runtime is only in `run_dmft.pbs`.

## DMFT checks

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml check dmft
```

`doctor` checks files and stage assumptions. `check` evaluates parsed `info.iterate` convergence diagnostics. The Matsubara baseline `dmft/CASE.indmfl` is expected to retain flag `1`.

## MaxEnt analytic continuation

Prepare only:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-maxent
```

The validated preparation is deliberately explicit:

```text
last N sig.inp.*.<impurity>
        ↓
official saverage.py
        ↓
Sig.average
        +
maxent_params.dat
```

The `Sig.average` name is intentional. Upstream examples use both `sig.inpx` and an explicit `-o Sig.average`; this workflow follows the cluster procedure that has already been validated locally.

Inspect before freezing a PBS script:

```bash
cat dmft/maxent/selected_sigmas.txt
head dmft/maxent/Sig.average
cat dmft/maxent/maxent_params.dat
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
```

After any desired edit of `maxent_params.dat`:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
```

The generated MaxEnt PBS mirrors the validated cluster script:

```text
source Intel compilervars
activate the configured conda environment
set WIENROOT / WIEN_DMFT_ROOT / MKL / MPI runtime libraries
write mpi_prefix.dat
python $WIEN_DMFT_ROOT/maxent_run.py Sig.average > sig1.out 2>&1
```

Then submit manually:

```bash
qsub dmft/maxent/run_maxent.pbs
```

**Important:** `maxent_run.py` does not read `mpi_prefix.dat`. The validated direct-Python launch therefore reports `Running in parallel mode rank=0 size=1`; it is a serial MaxEnt baseline. The file is retained because it matches the proven eDMFT job environment, not because it parallelizes MaxEnt. Do not wrap this Python command with Intel `mpirun` unless the exact Python environment's `mpi4py` has first been verified to use Intel MPI. A Python environment whose `mpi4py` was built with Open MPI will warn or fail if forced through Intel MPI.

For this validated baseline use:

```toml
[pbs_maxent]
ppn = 1
```

The required continuation output is `dmft/maxent/Sig.out`.

## Real-axis DOS

After `dmft/maxent/Sig.out` exists:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dos
```

`prepare-dos`:

1. copies the converged `dmft/` state into `dmft/onreal/` with upstream `dmft_copy.py` semantics;
2. supplements local WIEN2k potential files;
3. copies `maxent/Sig.out` to `onreal/sig.inp`;
4. saves `CASE.indmfl.matsubara`;
5. changes the copied `CASE.indmfl` Matsubara flag **1 -> 0**;
6. updates `nomega`, `omega_min`, `omega_max` for the real axis;
7. writes `indmfl.diff`, `prepare.log`, and `manifest.json`.

Inspect and freeze:

```bash
cat dmft/onreal/indmfl.diff
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dos
cat dmft/onreal/run_dos.pbs
qsub dmft/onreal/run_dos.pbs
```

The numerical PBS contains the official sequence:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

The Matsubara parent `dmft/CASE.indmfl` remains unchanged at flag `1`; only `onreal/CASE.indmfl` is converted to flag `0`.

## Band spectral function A(k,w)

Band preparation is independent of DOS and starts again from the converged Matsubara `dmft/` snapshot:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-band
```

It copies `Sig.out -> band/sig.inp`, installs `CASE.klist_band`, preserves `CASE.indmfl.matsubara`, changes the copied `CASE.indmfl` flag **1 -> 0**, updates the real-frequency mesh, and writes `indmfl.diff`, `prepare.log`, and `manifest.json`.

```bash
cat dmft/band/indmfl.diff
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs band
cat dmft/band/run_band.pbs
qsub dmft/band/run_band.pbs
```

The PBS directly runs:

```text
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

## Stage doctors and status

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor env
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
python ~/apps/edmft_workflow/workflow.py -c config.toml status
```

For real-axis stages the doctors explicitly verify that the active `indmfl` has flag `0` while the preserved Matsubara backup still has flag `1`. MaxEnt and real-axis doctors also validate self-energy tables and provenance manifests. Band doctor checks k-point/eigvals consistency when outputs exist.

## Common analysis

After `run_dmft.py`, common read-only analysis can already be run:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all
```

It creates convergence history/plots under:

```text
dmft/analysis/convergence/
```

including occupancy, `|n_latt-n_imp|`, chemical potential, and double-counting histories.

It also creates Matsubara self-energy plots and a low-frequency diagnostic table under:

```text
dmft/analysis/self_energy/
```

including:

```text
z_mass_diagnostic.csv
```

with the fitted `d ImSigma(iwn) / d wn`, fit `R^2`, diagnostic `Z`, and `1/Z`. These are diagnostics only; the workflow deliberately reports the fit quality instead of silently treating every low-frequency self-energy as Fermi-liquid linear.

When real-axis DOS and band outputs exist, the same command also produces DOS/local spectral/hybridization plots and `A(k,w)`. A human-readable summary is written to:

```text
dmft/analysis/summary.md
```

## Safety rules

- workflow never calls `qsub`;
- workflow never mutates the user's login shell or persistent shell configuration;
- prepare helpers do not source Intel/MKL or rewrite PATH/LD_LIBRARY_PATH/PYTHONPATH;
- full Intel/MKL/MPI setup is confined to generated heavy-job scripts (or explicit legacy foreground numerical runners);
- generated PBS files are standalone and contain resolved official commands;
- PBS jobs do not import this project or read `config.toml` at runtime;
- preparation and PBS generation are separate so native inputs can be inspected/edited first;
- stage preparation never modifies the upstream calculation directory;
- existing non-empty stage directories require `--force` and are backed up before recreation;
- native input files are copied, never moved;
- real-axis conversion is performed only on copied `indmfl` files; the converged Matsubara `dmft/CASE.indmfl` remains unchanged;
- `manifest.json` records hashes of critical source/control files and is refreshed when the PBS script is frozen.

## Upstream

This project is an independent workflow layer around WIEN2k and Kristjan Haule's eDMFT project: `https://github.com/ru-ccmt/eDMFT`.
