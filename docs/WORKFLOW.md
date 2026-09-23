# Fixed workflow and directory contract

This project intentionally fixes the calculation layout.  A material/project directory has exactly two top-level calculation directories:

```text
PROJECT/
├── dft/
│   └── tmp/                 # ordinary WIEN2k SCRATCH
└── dmft/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── results/
```

The workflow has **two mandatory manual checkpoints**:

1. `init_lapw` is run manually in `PROJECT/dft`.
2. `init_dmft.py` is run manually in `PROJECT/dmft`.

Everything else is eligible for automation.

## Stage 0: create the layout

```bash
edmft-workflow -c config.toml init-layout
```

This creates `dft/`, `dmft/`, and the configured DFT scratch directory.  It does not initialize either WIEN2k or DMFT.

## Stage 1: manual WIEN2k initialization

```bash
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
mkdir -p "$SCRATCH"
init_lapw
```

`init_lapw` stays manual because it is interactive and because choices such as RMT reduction, XC potential, spin polarization, and k mesh must remain explicit scientific decisions.

## Stage 2: automated DFT run

After `init_lapw` is complete:

```bash
edmft-workflow -c config.toml submit dft
```

or in the current shell/job:

```bash
edmft-workflow -c config.toml run dft
```

The DFT stage runs in `PROJECT/dft` and exports the configured `project.scratch_dir`, whose default convention is:

```text
PROJECT/dft/tmp
```

## Stage 3: prepare the DMFT working directory

After the DFT calculation is converged:

```bash
edmft-workflow -c config.toml prepare-dmft
```

This invokes `dmft_copy.py PROJECT/dft` from inside `PROJECT/dmft` to populate the DMFT working directory.

It deliberately stops here.

## Stage 4: manual DMFT initialization

```bash
cd PROJECT/dmft
init_dmft.py
```

`init_dmft.py` stays manual because selecting the correlated atoms/shells, projector energy window, `qsplit`, and related choices are part of the physical model rather than bookkeeping.

After checking/editing `case.indmf*`, `case.indmfl`, `case.indmfi`, and `params.dat`, continue with the automated runner.

## Stage 5: automated charge-self-consistent DFT+DMFT

```bash
edmft-workflow -c config.toml submit dmft
```

or:

```bash
edmft-workflow -c config.toml run dmft
```

The workflow executes Haule's `run_dmft.py` in `PROJECT/dmft`.

## Important SCRATCH distinction

There are two different behaviors that should not be conflated.

### Ordinary WIEN2k DFT

For the ordinary WIEN2k calculation in `dft/`, the workflow uses:

```text
SCRATCH = PROJECT/dft/tmp
```

This is the location configured by `project.scratch_dir`.

### Haule eDMFT Python drivers

In the current upstream eDMFT `src/python/utils.py`, `W2kEnvironment` sets:

```python
self.SCRATCH = '.'
```

rather than reading the shell `$SCRATCH` variable.  Consequently, `x_dmft.py` and `run_dmft.py` treat their **current working directory** as their effective vector scratch location.

Therefore the workflow does **not** attempt to force all eDMFT stages to use `dft/tmp`:

```text
run_dmft.py     -> PROJECT/dmft
x_dmft.py DOS   -> PROJECT/dmft/onreal
x_dmft.py band  -> PROJECT/dmft/band
```

This keeps each stage self-contained and avoids mixing DFT `vector/energy` files with real-axis or band-path versions.

## Stage 6: post-processing

After convergence checks pass:

```bash
edmft-workflow -c config.toml submit post
```

which chains:

```text
maxent -> onreal/DOS -> band/A(k,w)
```

The resulting layout is:

```text
PROJECT/dmft/
├── info.iterate
├── sig.inp.*
├── imp.0/
├── maxent/
│   ├── Sig.average
│   ├── maxent_params.dat
│   └── Sig.out
├── onreal/
│   ├── case.vsp
│   ├── case.vns
│   ├── case.vector
│   ├── case.energy
│   ├── case.cdos
│   ├── case.gc1
│   ├── case.dlt1
│   └── case.Eimp1
├── band/
│   ├── case.vsp
│   ├── case.vns
│   ├── case.klist_band
│   ├── case.vector
│   ├── case.energy
│   └── eigvals.dat
└── results/
```

## Why `case.vsp` and `case.vns` are copied explicitly

`x_dmft.py lapw1` and `x_dmft.py lapw1 --band` require WIEN2k potential files in the working directory. `dmft_copy.py` does not guarantee that every later post-processing directory contains those potential files.

For this reason `edmft_workflow` explicitly copies from the parent `dmft/` directory into both `onreal/` and `band/`:

```text
case.vsp
case.vns
```

and, when present, their spin-polarized variants (`vspup/vspdn`, `vnsup/vnsdn`).

This prevents the failure mode in which `lapw1 --band` is launched correctly but cannot find the converged potential.
