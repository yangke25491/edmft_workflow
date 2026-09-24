# Fixed workflow and directory contract

A project has one project-local `config.toml`, optional native scientific inputs, and isolated stage directories:

```text
PROJECT/
├── config.toml
├── inputs/
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
    ├── analysis/
    └── results/
```

The workflow has exactly **two mandatory manual scientific checkpoints**, both performed in the complete WIEN2k DFT directory:

1. `init_lapw` in `PROJECT/dft` before the DFT run.
2. `init_dmft.py` in the same `PROJECT/dft` after DFT convergence.

The workflow manager never calls `qsub`.

## Operating model

Every production stage follows the same transparent pattern:

```text
prepare native files
       ↓
doctor / inspect / edit
       ↓
freeze standalone PBS
       ↓
inspect PBS
       ↓
manual qsub
```

The generated PBS contains resolved WIEN2k/eDMFT commands and environment setup. It does **not** import `edmft_workflow` and does **not** read `config.toml` at runtime.

## Stage 0: create layout

```bash
python /path/to/edmft_workflow/workflow.py -c config.toml init-layout
```

This creates `inputs/`, `dft/`, `dmft/`, and DFT scratch directories. It does not run either initializer.

## Stage 1: manual WIEN2k initialization

```bash
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
mkdir -p "$SCRATCH"
init_lapw
```

`init_lapw` stays manual because choices such as RMT reduction, XC potential, spin polarization, and k mesh are scientific decisions.

## Stage 2: DFT

Generate a standalone DFT PBS script:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml pbs dft
cat PROJECT/dft/run_dft.pbs
qsub PROJECT/dft/run_dft.pbs
```

The user performs the `qsub` manually.

## Stage 3: manual DMFT initialization in converged DFT directory

After DFT convergence:

```bash
cd PROJECT/dft
init_dmft.py
```

Inspect:

```text
CASE.indmf
CASE.indmfl
CASE.indmfi
```

The initializer is intentionally run in the full converged DFT directory so it sees the actual WIEN2k state.

A useful validation is:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml doctor dft
```

The Matsubara `CASE.indmfl` flag is expected to be `1`.

## Stage 4: isolated DMFT snapshot

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml prepare-dmft
```

The stage:

1. validates DFT and manual `init_dmft.py` outputs before touching `dmft/`;
2. backs up an existing non-empty `dmft/` when `--force` is explicitly used;
3. runs `dmft_copy.py PROJECT/dft` from inside `PROJECT/dmft`;
4. supplements state omitted by upstream `dmft_copy.py`, including `CASE.indmf` and converged potential files;
5. prefers `PROJECT/inputs/params.dat`; otherwise generates a fallback from config;
6. runs official `szero.py` for the initial `sig.inp` unless configured otherwise;
7. creates `dmft/tmp/`;
8. performs a final READY gate.

There is deliberately no `DFT_SOURCE` symlink and no full recursive DFT copy. Large transient `vector*`/`energy*` files are not duplicated just for archival completeness.

After inspection:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml pbs dmft
cat PROJECT/dmft/run_dmft.pbs
qsub PROJECT/dmft/run_dmft.pbs
```

## Stage 5: check converged DMFT

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml doctor dmft
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml check dmft
```

`doctor` checks files and stage assumptions. `check` reports parsed `info.iterate` quantities including `mu`, `Vdc`, `n_latt`, `n_imp`, occupancy mismatch, and outer-cycle drift.

The converged baseline `PROJECT/dmft/CASE.indmfl` must remain on the Matsubara axis with flag `1`.

## Stage 6: MaxEnt analytic continuation

Prepare native inputs only:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml prepare-maxent
```

Sequence:

```text
last N sig.inp.*.<impurity>
        ↓
official saverage.py
        ↓
sig.inpx
        +
maxent_params.dat
```

`PROJECT/inputs/maxent_params.dat` is preferred. If absent, the current upstream `maxent_run.py` default template is written explicitly to `dmft/maxent/maxent_params.dat`.

Inspect/edit:

```bash
cat dmft/maxent/selected_sigmas.txt
cat dmft/maxent/maxent_params.dat
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml doctor maxent
```

Then freeze the PBS:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

The native compute command is:

```text
maxent_run.py sig.inpx
```

with required output:

```text
Sig.out
```

MaxEnt does **not** modify `CASE.indmfl`.

## Stage 7: real-axis DOS

After `Sig.out` exists:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml prepare-dos
```

Preparation starts from the converged Matsubara `dmft/` snapshot, creates `onreal/`, copies `Sig.out -> sig.inp`, preserves `CASE.indmfl.matsubara`, and changes only the copied real-axis file:

```text
CASE.indmfl second line:
Matsubara flag 1 -> 0
nomega          -> configured DOS mesh
omega_min/max   -> configured real-axis window
```

The exact change is written to:

```text
onreal/indmfl.diff
```

Inspect and freeze:

```bash
cat dmft/onreal/indmfl.diff
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml doctor dos
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml pbs dos
cat dmft/onreal/run_dos.pbs
qsub dmft/onreal/run_dos.pbs
```

The standalone PBS contains the official numerical sequence:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

The parent `dmft/CASE.indmfl` remains at flag `1`.

## Stage 8: A(k,w) / band spectra

Band preparation starts independently from the converged Matsubara `dmft/` snapshot, not from `onreal/`:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml prepare-band
```

The band path is taken in this priority order:

```text
PROJECT/inputs/CASE.klist_band
        ↓ if absent
band.klist_source from config
        ↓ if absent
STOP
```

The copied `band/CASE.indmfl` is preserved as `CASE.indmfl.matsubara` and converted `1 -> 0` with the configured band real-frequency mesh.

Inspect and freeze:

```bash
cat dmft/band/indmfl.diff
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml doctor band
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml pbs band
cat dmft/band/run_band.pbs
qsub dmft/band/run_band.pbs
```

The standalone PBS directly runs:

```text
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

Band doctor checks the k-list point count and, once outputs exist, compares it with `outputdmfp`/`eigvals.dat` information.

## Provenance manifests

`prepare-maxent`, `prepare-dos`, and `prepare-band` write `manifest.json` containing SHA256 hashes of critical source/control files.

When `pbs STAGE` is run, the manifest is refreshed. This is the **freeze point**: it records the exact inspected input files that correspond to the standalone PBS script.

If a scientific input is changed after that point, `doctor STAGE` reports a manifest mismatch. Re-run:

```bash
python workflow.py -c config.toml pbs STAGE --force
```

to freeze the new state before manual submission.

## Common analysis

Read-only analysis can be run in the foreground:

```bash
python /path/to/edmft_workflow/workflow.py -c PROJECT/config.toml analyze all
```

It produces:

```text
dmft/analysis/convergence/
    info_iterate_outer.csv
    occupancy.png/pdf
    occupancy_difference.png/pdf
    chemical_potential.png/pdf
    double_counting.png/pdf

dmft/analysis/self_energy/
    sigma_matsubara_real.png/pdf
    sigma_matsubara_imag.png/pdf
    z_mass_diagnostic.csv
    sigma_realaxis_real.png/pdf      # when Sig.out exists
    sigma_realaxis_imag.png/pdf      # when Sig.out exists

dmft/analysis/summary.md
```

The reported `Z` and `1/Z` values are low-frequency linear-fit diagnostics. The fit `R^2` and fit-point count are written alongside them; the workflow does not silently assume every self-energy is in a clean Fermi-liquid regime.

When DOS and band outputs exist, the analysis command also invokes the existing DOS/local-spectral/hybridization and `A(k,w)` plotting helpers.

## Safety invariants

- no workflow command calls `qsub`;
- production PBS scripts are standalone and do not read `config.toml` at runtime;
- `dft/` is not modified by DMFT preparation;
- `dmft/` is not modified by MaxEnt/DOS/band preparation;
- DOS and band are independent sibling real-axis directories;
- upstream files are copied, never moved;
- real-axis `1 -> 0` conversion occurs only in copied `indmfl` files;
- `--force` backs up a non-empty stage directory before recreation;
- manifests track critical source/control files at preparation and PBS freeze points.
