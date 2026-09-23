# edmft_workflow

Automation for the post-`run_dmft.py` part of a **WIEN2k + Haule eDMFT** workflow.

The goal is to replace fragile manual file juggling with reproducible stages that have explicit sanity checks:

```text
converged run_dmft.py
        |
        +--> convergence check (info.iterate)
        |
        +--> MaxEnt: Sigma(iwn) -> Sigma(w)
        |
        +--> real-axis DOS / Gloc / Delta
        |
        +--> band spectral function A(k,w)
        |
        +--> PNG/PDF figures
```

## Scope of v0.1

This version assumes the main charge-self-consistent DFT+DMFT run has already finished. It automates:

- parsing `info.iterate` and warning on poor `n_latt`/`n_imp` agreement;
- selecting the last N self-energy iterations and checking that their Matsubara grids match;
- `saverage.py` + `maxent_params.dat` + `maxent_run.py`;
- a clean real-axis DOS directory using `dmft_copy.py`, real-axis `case.indmfl`, `lapw0`, `x_dmft.py lapw1`, and `x_dmft.py dmft1`;
- a separate band directory using `case.klist_band`, `x_dmft.py lapw1 --band`, and `x_dmft.py dmftp`;
- k-point consistency checks between `case.klist_band`, `case.outputdmfp`, and `eigvals.dat`;
- independent DOS, local spectral function, hybridization, self-energy, and `A(k,w)` figures;
- PBS/Torque script generation and dependency-chained submission.

It deliberately keeps **MaxEnt**, **DOS**, and **band** in separate directories so `sig.inp`, `case.vector`, `case.energy`, and `case.indmfl` cannot silently overwrite each other.

## Why the A(k,w) plotter does not require `cakw`

Haule's official `wakplot.py` calls the compiled `cakw` extension. The implementation in this project reproduces the same unit-coherence-factor expression in NumPy:

```text
A(k,w) = sum_b -Im[1 / (w + mu - e_b(k,w))] / pi
```

with the same `small=1e-5` lower bound on the negative imaginary part. This makes plotting headless and removes the `cakw`/`PYTHONPATH` problem for the common case where `cohfactorsd.dat` is absent.

If `cohfactorsd.dat` is present, v0.1 intentionally stops and asks you to use the official plotting path rather than silently ignoring coherence factors.

## Installation

On the cluster:

```bash
git clone <your-private-repo-url>
cd edmft_workflow
python -m pip install -e .
```

For tests:

```bash
python -m pip install -e '.[dev]'
pytest -q
```

## Configuration

Copy the example:

```bash
cp config.example.toml config.toml
vim config.toml
```

`config.toml` is gitignored. The tracked `examples/mno/config.toml` shows the MnO benchmark values used during development.

For site-specific Intel/MKL/MPI setup, put the real setup in a separate file, for example:

```text
~/.config/edmft_workflow/env.sh
```

and set:

```toml
[environment]
setup_script = "/home/USER/.config/edmft_workflow/env.sh"
```

This is preferable to duplicating compiler/MPI environment code into every PBS script.

## First commands

Check that the main DMFT directory looks usable:

```bash
edmft-workflow -c config.toml doctor
```

Check convergence:

```bash
edmft-workflow -c config.toml check
```

Typical output:

```text
DMFT convergence: PASS
last outer cycle       : 15
last charge iteration  : 7
mu                     : 6.531263000 eV
n_latt                 : 5.031487000
n_imp                  : 5.030921000
|n_latt-n_imp|         : 0.000566000
```

The `max_dn` threshold in the config is a workflow sanity criterion, not a universal physics threshold.

## Run the whole post-processing chain in the current shell/job

```bash
edmft-workflow -c config.toml run all --force
```

This performs:

```text
maxent -> DOS -> band -> plots
```

For debugging, run stages separately:

```bash
edmft-workflow -c config.toml run maxent --force
edmft-workflow -c config.toml run dos --force
edmft-workflow -c config.toml run band --force
```

`--force` never deletes an existing stage directory directly. It renames it to a timestamped backup first.

## PBS/Torque

Write but do not submit a PBS script:

```bash
edmft-workflow -c config.toml pbs maxent
edmft-workflow -c config.toml pbs dos
edmft-workflow -c config.toml pbs band
```

Submit one stage:

```bash
edmft-workflow -c config.toml submit maxent --force
```

Submit the full chain with `afterok` dependencies:

```bash
edmft-workflow -c config.toml submit all --force
```

Conceptually:

```text
maxent_job --afterok--> dos_job --afterok--> band_job
```

## Directory layout produced by the workflow

```text
<work_root>/
├── maxent/
│   ├── Sig.average
│   ├── maxent_params.dat
│   ├── Sig.out
│   ├── saverage.log
│   └── maxent.log
├── onreal/
│   ├── sig.inp              # real-axis Sigma(w)
│   ├── case.indmfl          # matsubara=0
│   ├── case.vector
│   ├── case.energy
│   ├── case.cdos
│   ├── case.gc1
│   ├── case.dlt1
│   └── case.Eimp1
├── band/
│   ├── case.klist_band
│   ├── case.vector
│   ├── case.energy
│   ├── eigvals.dat
│   └── case.outputdmfp
└── results/
    ├── sigma_matsubara.png/.pdf
    ├── sigma_realaxis.png/.pdf
    ├── dos_total.png/.pdf
    ├── spectral_local.png/.pdf
    ├── hybridization.png/.pdf
    ├── Akw.png/.pdf
    └── akw_data.npz
```

## MaxEnt behavior

The workflow:

1. identifies `sig.inp.<outer>.<impurity>` files numerically;
2. takes only the last `maxent.average_last` files;
3. verifies identical Matsubara grids;
4. creates `maxent_params.dat`;
5. runs `saverage.py` with an explicit file list;
6. runs `maxent_run.py Sig.average`;
7. refuses to continue unless `Sig.out` exists and is non-empty.

The default MaxEnt parameter values reproduce the parameter set used in the MnO tutorial-style workflow, but they are all configurable.

## DOS behavior

The DOS stage creates a clean `onreal/` directory and performs:

```text
dmft_copy.py <converged DMFT dir>
cp maxent/Sig.out sig.inp
patch case.indmfl: matsubara=0, DOS real-frequency window
x lapw0 -f case
x_dmft.py lapw1
x_dmft.py dmft1
```

It then requires:

```text
case.vector != 0 bytes
case.energy != 0 bytes
DMFT1 END
case.cdos
case.gc1
case.dlt1
case.Eimp1
```

This catches the common empty-`case.vector` failure before the Fortran `unit 9` error appears.

## Band behavior

The band stage creates a clean `band/` directory and performs:

```text
dmft_copy.py <onreal dir>
copy Sig.out -> sig.inp
copy/customize case.klist_band
patch case.indmfl: matsubara=0, band real-frequency window
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

The important guardrails are:

- `lapw1.def` must reference `case.klist_band`;
- newly generated `case.vector` and `case.energy` must be non-empty;
- the maximum `Finished k-point number` in the captured `dmftp` output must equal the number of k points in `case.klist_band` when that marker is available;
- `numkpt` and `tot-k` in `case.outputdmfp` must agree with the k-path when present;
- the number of k-point blocks in `eigvals.dat` must equal the k-path length.

This is specifically designed to catch a dangerous failure mode where a 111-point `klist_band` is accidentally combined with an old 20-point IBZ `vector/energy` set.

## k-paths

For the WIEN2k FCC template:

```toml
[band]
klist_source = "@wien:fcc.klist"
```

which expands to:

```text
$WIENROOT/SRC_templates/fcc.klist
```

For a custom path:

```toml
[band]
klist_source = "/absolute/path/to/my.klist_band"
```

The workflow does not invent a crystallographic path; it copies exactly the k-list you provide.

## Plotting philosophy

Each physical quantity is written as a separate figure, not subplots:

- total/projected DOS;
- local orbital spectral function `-Im G / pi`;
- hybridization `-Im Delta`;
- Matsubara self-energy;
- real-axis self-energy;
- band spectral function `A(k,w)`.

This keeps publication/slide post-processing simple.

## Important limitations in v0.1

- The automated `A(k,w)` plotter assumes no `cohfactorsd.dat`. If coherence factors are present it stops rather than giving a misleading plot.
- The self-energy selector currently targets one impurity index (`maxent.impurity`, default `1`). Multi-impurity continuation can be added next.
- Spin-polarized/SOC-specific filename combinations are not yet abstracted. The workflow preserves files created by `dmft_copy.py`, but v0.1 is primarily validated against the paramagnetic MnO-style route.
- `run_dmft.py` itself is not yet launched by this project. v0.1 starts after the main DFT+DMFT run has finished.

## Upstream references

This project wraps/validates the file conventions used by Kristjan Haule's eDMFT project:

- https://github.com/ru-ccmt/eDMFT
- `src/python/x_dmft.py`
- `src/python/wakplot.py`
- `src/putils/akplt/cakw.cc`

It is an independent workflow/automation layer, not a fork of eDMFT.
