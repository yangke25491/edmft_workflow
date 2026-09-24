# edmft_workflow

A transparent workflow manager for **WIEN2k + Kristjan Haule eDMFT** on PBS/Torque clusters.

The project deliberately avoids hiding the official commands behind long Python call chains. It prepares stage directories, checks files/results, writes native eDMFT/WIEN2k inputs, and generates **standalone PBS scripts**. The user inspects each PBS file and runs `qsub` manually.

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
    ├── onreal/
    ├── band/
    └── results/
```

No numerical stage uses symlinks back into an upstream calculation directory. Mutable WIEN2k/eDMFT state is copied so that `dft/`, `dmft/`, `maxent/`, `onreal/`, and `band/` stay independent.

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

Then prepare the isolated DMFT snapshot:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

`prepare-dmft` validates converged DFT + `indmf/indmfl/indmfi`, runs `dmft_copy.py SOURCE` from inside `dmft/`, supplements potential files omitted by upstream `dmft_copy.py`, prepares `params.dat`, runs official `szero.py` for the initial `sig.inp`, and performs a final READY gate.

## DMFT checks

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml check dmft
```

`doctor` checks files and stage assumptions. `check` evaluates the parsed `info.iterate` convergence diagnostics.

## MaxEnt analytic continuation

Prepare only:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-maxent
```

The preparation follows the upstream logic:

```text
last N sig.inp.*.<impurity>
        ↓
official saverage.py
        ↓
sig.inpx
        +
maxent_params.dat
        ↓
run_maxent.pbs
```

Inspect everything before submission:

```bash
cat dmft/maxent/selected_sigmas.txt
cat dmft/maxent/maxent_params.dat
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

The PBS script directly runs upstream `maxent_run.py sig.inpx`; it does not call `workflow.py` and does not read `config.toml` at runtime.

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
7. writes `indmfl.diff` and standalone `run_dos.pbs`.

Inspect and submit manually:

```bash
cat dmft/onreal/indmfl.diff
cat dmft/onreal/run_dos.pbs
qsub dmft/onreal/run_dos.pbs
```

The numerical PBS contains the official sequence:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

## Band spectral function A(k,w)

Band preparation is independent of DOS and starts again from the converged Matsubara `dmft/` snapshot:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-band
```

It copies `Sig.out -> band/sig.inp`, installs `CASE.klist_band`, preserves `CASE.indmfl.matsubara`, changes the copied `CASE.indmfl` flag **1 -> 0**, updates the real-frequency mesh, and writes `indmfl.diff` plus `run_band.pbs`.

```bash
cat dmft/band/indmfl.diff
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
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
python ~/apps/edmft_workflow/workflow.py -c config.toml status
```

For real-axis stages the doctors explicitly verify that the active `indmfl` has flag `0` while the preserved Matsubara backup still has flag `1`.

## Analysis

After outputs exist:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all
```

Current analysis includes Matsubara/real-axis self-energy plots, DOS/local spectral function/hybridization plots, and A(k,w) when the required band outputs are available.

## Safety rules

- workflow never calls `qsub`;
- generated PBS files are standalone and contain resolved official commands;
- stage preparation never modifies the upstream calculation directory;
- existing non-empty stage directories require `--force` and are backed up before recreation;
- native input files are copied, never moved;
- real-axis conversion is performed only on copied `indmfl` files; the converged Matsubara `dmft/CASE.indmfl` remains unchanged.

## Upstream

This project is an independent workflow layer around WIEN2k and Kristjan Haule's eDMFT project: `https://github.com/ru-ccmt/eDMFT`.
