# Fixed workflow and directory contract

A material/project has one project-local `config.toml` and two calculation directories:

```text
PROJECT/
├── config.toml
├── dft/
│   └── tmp/                 # ordinary WIEN2k SCRATCH
└── dmft/
    ├── tmp/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── results/
```

The workflow has exactly **two mandatory manual scientific checkpoints**, and both are performed in the full WIEN2k DFT directory:

1. `init_lapw` in `PROJECT/dft` before the DFT run.
2. `init_dmft.py` in the same `PROJECT/dft` after DFT convergence.

Everything else is automated.

## Stage 0: create the layout

```bash
python /path/to/edmft_workflow/workflow.py -c config.toml init-layout
```

This creates `dft/`, `dmft/`, and the DFT scratch directory. It does not run either initializer.

## Stage 1: manual WIEN2k initialization

```bash
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
mkdir -p "$SCRATCH"
init_lapw
```

`init_lapw` stays manual because choices such as RMT reduction, XC potential, spin polarization, and k mesh are scientific decisions.

## Stage 2: automated DFT run

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml submit dft
```

The DFT stage runs in `PROJECT/dft`, with ordinary WIEN2k scratch at `PROJECT/dft/tmp` unless configured otherwise.

## Stage 3: manual DMFT initialization in the converged DFT directory

After DFT has converged, stay in the complete WIEN2k working directory:

```bash
cd PROJECT/dft
init_dmft.py
```

This is intentionally done **before** `prepare-dmft`. Haule's initializer is designed to see the converged WIEN2k state directly; current upstream code checks, for example, whether `case.vsp` is present when constructing the projector-related input.

Inspect the generated model-definition files before continuing:

```text
case.indmf
case.indmfl
case.indmfi
```

`init_dmft.py` remains manual because correlated atoms/shells, projector window, qsplit, equivalent impurity grouping, and related choices define the physical model.

## Stage 4: automated isolated DMFT snapshot

Run from anywhere:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml prepare-dmft
```

The stage does the following in order:

1. validates the converged DFT state **before touching an existing `dmft/` directory**;
2. verifies that manual `init_dmft.py` has produced `case.indmf`, `case.indmfl`, and `case.indmfi` in `dft/`;
3. verifies the required WIEN2k inputs and a complete potential state (`vsp+vns`, or the complete spin-polarized potential set);
4. prepares a clean `dmft/` snapshot; an existing non-empty one is refused unless `--force` is used, in which case it is first moved to a timestamped backup;
5. runs `dmft_copy.py PROJECT/dft` **from inside `PROJECT/dmft`**;
6. explicitly copies state omitted by upstream `dmft_copy.py`, especially the original `case.indmf` and the converged `case.vsp/case.vns` or spin-polarized variants;
7. generates `params.dat` from `[dmft_params]` and `[impurityN]` sections of `config.toml`;
8. generates the initial `sig.inp` with the official `szero.py` unless configured otherwise;
9. creates `dmft/tmp/` for the production PBS scratch convention;
10. runs a final READY validation and returns success only when the prepared snapshot is complete.

There is deliberately **no `DFT_SOURCE` symlink**. The fixed sibling layout already records the relationship between `dft/` and `dmft/`, while avoiding a misleading runtime dependency on the DFT baseline.

The numerical DFT state is copied rather than symlinked because charge-self-consistent DMFT updates WIEN2k potentials and charge-density state. A writable symlink back into `dft/` could silently modify the pristine DFT baseline.

The workflow also deliberately does **not** clone the whole DFT directory. Large eigenvector/eigenvalue scratch products such as `case.vector*` and `case.energy*` are not copied merely for archival completeness; the snapshot contains the state required to launch the independent CSC-DMFT calculation.

A successful command ends with:

```text
DMFT snapshot READY
```

Only after this gate passes should `submit dmft` be used.

## `params.dat` mapping

Example configuration:

```toml
[dmft_params]
solver = "CTQMC"
DCs = "nominal"
max_dmft_iterations = 1
max_lda_iterations = 100

[impurity0]
U = 8.0
J = 0.8
beta = 100.0
nf0 = 5.0
M = 5000000
```

becomes conceptually:

```python
solver = 'CTQMC'
DCs = 'nominal'
max_dmft_iterations = 1
max_lda_iterations = 100

iparams0 = {
    'U': [8.0, ''],
    'J': [0.8, ''],
    'beta': [100.0, ''],
    'nf0': [5.0, ''],
    'M': [5000000, ''],
}
```

The workflow deliberately refuses to invent a non-empty `[impurity0]`; U, J, temperature/beta, nominal occupancy and solver controls must come from the user's validated physical setup.

## Initial `sig.inp`

By default:

```toml
[dmft_prepare]
initial_sigma = "szero"
szero_args = []
```

runs Haule's official `szero.py` in the prepared `dmft/` directory after `params.dat` is written. Extra command-line arguments can be supplied through `szero_args`, for example an explicit starting `Edc` or mesh size.

## Stage 5: charge-self-consistent DFT+DMFT

After inspecting the prepared snapshot:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml submit dmft
```

`run_dmft.py` is always executed in `PROJECT/dmft`, never in `dft/`. Before launching it, the workflow reuses the same READY snapshot validation so a partially modified `dmft/` directory cannot silently enter production.

This preserves the converged DFT directory as an independent baseline for comparison or restarting a new DMFT model.

## Scratch convention

Ordinary DFT uses:

```text
PROJECT/dft/tmp
```

The production DMFT PBS job exports:

```text
PROJECT/dmft/tmp
```

Post-processing stages remain self-contained in their own working directories (`onreal/`, `band/`) so vector/energy files from different k meshes cannot contaminate one another.

## Stage 6: post-processing

After convergence checks pass:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml submit post
```

which chains:

```text
maxent -> onreal/DOS -> band/A(k,w)
```

Plotting remains a fast foreground step.

## Why `case.vsp` and `case.vns` are copied explicitly

Current upstream `dmft_copy.py` intentionally copies a selected list of WIEN2k/DMFT files, but its DFT list does not include `case.vsp` or `case.vns`. `init_dmft.py` and later LAPW1-based operations can depend on the converged potential. Therefore `prepare-dmft` supplements upstream copying with explicit potential-state copies.

The same rule is used for `onreal/` and `band/`: working potentials are local copies, not symlinks into another mutable calculation directory.
