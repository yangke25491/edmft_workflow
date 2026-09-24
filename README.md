# edmft_workflow

A transparent workflow manager for **WIEN2k + Kristjan Haule eDMFT**.

The workflow automates repetitive file preparation, checks, and runtime setup while keeping the scientific inputs and native WIEN2k/eDMFT commands visible. It never calls `qsub` for you and never modifies the user's persistent shell environment.

## Execution model

The current production model is deliberately split into three classes:

```text
Heavy PBS jobs
  DFT
  DMFT
  MaxEnt

Foreground MPI postprocessing
  DOS
  Band / A(k,w)

Lightweight foreground work
  prepare-*
  doctor / check / status
  analyze / plots
```

Only DFT, DMFT, and MaxEnt generate PBS scripts. DOS and band run directly in a disposable foreground child shell with their own MPI setup. The parent/login shell is unchanged after the command exits.

## Directory layout

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

```text
PROJECT/dft : init_lapw
PROJECT/dft : init_dmft.py     # after DFT convergence
```

## Zero-interference policy

`prepare-*`, `doctor`, `check`, `status`, and `analyze` do not source Intel `compilervars.sh`, rewrite the user's shell, edit `.bashrc`, or install aliases. Lightweight upstream helpers such as `dmft_copy.py`, `szero.py`, and `saverage.py` are invoked with explicit paths in child processes.

Numerical execution owns its environment locally:

```text
PBS DFT/DMFT/MaxEnt
    → runtime exists only inside run_*.pbs

run dos / run band
    → runtime exists only inside a child bash process
    → exits cleanly when postprocessing finishes
```

## DFT

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml init-layout

cd dft
init_lapw
```

Generate the DFT PBS:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dft
cat dft/run_dft.pbs
qsub dft/run_dft.pbs
```

After convergence:

```bash
cd dft
init_dmft.py
```

## DMFT

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

The DMFT PBS uses the native Intel MPI stack and writes `mpi_prefix.dat` from the actual `$PBS_NODEFILE` allocation before running `run_dmft.py`.

## MaxEnt analytic continuation

Prepare:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-maxent
```

The preparation follows:

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
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
```

Generate the PBS:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

### MaxEnt MPI rule

The validated cluster setup has `mpi4py` built against **Open MPI**, while native WIEN2k/eDMFT uses Intel MPI. Therefore MaxEnt must not be launched with Intel `mpirun`.

The generated MaxEnt PBS mirrors the validated command:

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

The Intel compiler/MKL environment is still loaded because the installed eDMFT/Fortran extensions may need it, but the MaxEnt MPI launcher itself is explicitly the Open MPI launcher next to the configured Python environment.

Current `maxent_run.py` distributes work over active baths/channels. If the input reports `nb=2`, more than two MPI ranks generally do not provide useful bath-level parallelism. The workflow reports the active channel count after `prepare-maxent` so the requested PBS size can be chosen explicitly.

The required output is:

```text
dmft/maxent/Sig.out
```

## Real-axis DOS: foreground MPI

After `Sig.out` exists:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dos
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
```

`prepare-dos` copies the converged DMFT state into `dmft/onreal/`, copies `Sig.out -> sig.inp`, preserves `CASE.indmfl.matsubara`, and changes only the copied `CASE.indmfl` from Matsubara flag `1` to real-axis flag `0`.

Then run directly in foreground MPI:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml run dos
```

The child shell writes `mpi_prefix.dat` using `[foreground].dos_np` and executes:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

Logs are streamed to the terminal and saved as `lapw0.log`, `lapw1.log`, and `dmft1.log`.

## Band / A(k,w): foreground MPI

Prepare:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-band
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
```

Then:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml run band
```

The child shell writes `mpi_prefix.dat` using `[foreground].band_np` and executes:

```text
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

The main output is `dmft/band/eigvals.dat`.

## Foreground MPI configuration

Example:

```toml
[foreground]
dos_np = 8
band_np = 8
```

These ranks use the native MPI launcher configured under `[parallel]`, normally Intel MPI for WIEN2k/eDMFT.

## Core commands

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor env
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
python ~/apps/edmft_workflow/workflow.py -c config.toml status
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all
```

## Safety invariants

- workflow never calls `qsub`;
- workflow never modifies persistent shell configuration;
- preparation never mutates the upstream calculation directory;
- real-axis `indmfl` changes are made only in copied DOS/band directories;
- DFT/DMFT/MaxEnt PBS files are standalone and do not import this workflow at runtime;
- DOS/band foreground MPI runs happen in disposable child shells;
- MaxEnt uses the MPI implementation matching its `mpi4py` environment;
- native WIEN2k/eDMFT stages keep their native Intel MPI runtime;
- `--force` backs up existing non-empty prepared stage directories before recreating them.

## Upstream

This project is an independent workflow layer around Kristjan Haule's eDMFT project and WIEN2k.
