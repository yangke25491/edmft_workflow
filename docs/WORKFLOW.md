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
└── dmft/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── analysis/
```

The two scientific initializers remain manual:

1. `init_lapw` in `PROJECT/dft` before the DFT run.
2. `init_dmft.py` in `PROJECT/dft` after DFT convergence.

The workflow never calls `qsub` and never modifies persistent shell configuration.

## Execution classes

```text
PBS heavy jobs
  dft
  dmft
  maxent

Foreground MPI
  dos
  band

Lightweight foreground
  prepare-*
  doctor / check / status
  analyze / plots
```

Preparation helpers use explicit paths and a minimal child-only environment. Numerical jobs/runs create their full runtime only inside the PBS script or disposable foreground child shell.

## DFT

```bash
python workflow.py -c config.toml init-layout
cd dft
init_lapw
```

Then:

```bash
python workflow.py -c config.toml pbs dft
cat dft/run_dft.pbs
qsub dft/run_dft.pbs
```

After DFT convergence:

```bash
cd dft
init_dmft.py
```

## DMFT

```bash
python workflow.py -c config.toml prepare-dmft
python workflow.py -c config.toml doctor dmft
python workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

The DMFT PBS writes `mpi_prefix.dat` from `$PBS_NODEFILE` and uses the native Intel MPI runtime.

## MaxEnt

```bash
python workflow.py -c config.toml prepare-maxent
```

Preparation performs:

```text
last N sig.inp.*.<impurity>
        ↓
saverage.py
        ↓
sig.inpx
        +
maxent_params.dat
```

Inspect:

```bash
head dmft/maxent/sig.inpx
cat dmft/maxent/maxent_params.dat
python workflow.py -c config.toml doctor maxent
```

Generate and submit:

```bash
python workflow.py -c config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

The validated MaxEnt launch uses the Open MPI runtime installed beside the configured Python environment, because that environment's `mpi4py` is Open-MPI based:

```bash
ENV=/home/USER/miniforge3/envs/edmft
MPI="$ENV/bin/mpirun"
NP=$(wc -l < "$PBS_NODEFILE")

echo "$MPI -np $NP" > mpi_prefix.dat

"$MPI" -np "$NP" \
    "$ENV/bin/python" \
    "$WIEN_DMFT_ROOT/maxent_run.py" \
    sig.inpx > sig1.out 2>&1
```

Do not replace this launcher with Intel `mpirun` unless `mpi4py` has been built against Intel MPI. The MaxEnt MPI scaling is limited by the number of active baths/channels; for `nb=2`, two MPI ranks are the useful bath-level limit.

Required output:

```text
dmft/maxent/Sig.out
```

## Real-axis DOS: foreground MPI

After `Sig.out` exists:

```bash
python workflow.py -c config.toml prepare-dos
python workflow.py -c config.toml doctor dos
python workflow.py -c config.toml run dos
```

`prepare-dos` creates `dmft/onreal/`, copies `Sig.out -> sig.inp`, preserves `CASE.indmfl.matsubara`, and changes only the copied `CASE.indmfl` from Matsubara flag `1` to real-axis flag `0`.

`run dos` starts a disposable child shell, loads the native Intel/MKL/MPI runtime, writes `mpi_prefix.dat` using `[foreground].dos_np`, and runs:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

The logs are shown live and saved in `dmft/onreal/`.

## Band / A(k,w): foreground MPI

Prepare:

```bash
python workflow.py -c config.toml prepare-band
python workflow.py -c config.toml doctor band
python workflow.py -c config.toml run band
```

The band path is taken from `inputs/CASE.klist_band` when present, otherwise from the explicit `band.klist_source` configuration.

`run band` writes `mpi_prefix.dat` using `[foreground].band_np` and runs:

```text
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

Main output:

```text
dmft/band/eigvals.dat
```

## Foreground MPI configuration

```toml
[foreground]
dos_np = 8
band_np = 8
```

These stages use the native MPI launcher under `[parallel]`, normally Intel MPI.

## Provenance and safety

- `prepare-*` never modifies the upstream calculation directory.
- `dmft/CASE.indmfl` remains Matsubara flag `1`.
- DOS/band modify only copied real-axis `indmfl` files.
- `--force` backs up non-empty prepared stage directories.
- MaxEnt/DOS/band manifests record critical prepared/source files.
- DFT/DMFT/MaxEnt PBS scripts are standalone and do not read `config.toml` at runtime.
- DOS/band foreground runs happen in child shells and do not alter the parent shell.

## Analysis

```bash
python workflow.py -c config.toml analyze all
```

This produces convergence, self-energy, DOS/local-spectrum, and `A(k,w)` diagnostics when the required outputs exist.
