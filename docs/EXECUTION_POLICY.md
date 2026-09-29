# Execution policy: zero-interference preparation and standalone PBS

Only two scientific initialization steps are intentionally manual, and both are performed in the complete DFT directory:

```text
PROJECT/dft : init_lapw      # before DFT
PROJECT/dft : init_dmft.py   # after DFT convergence
```

The workflow is designed to reduce WIEN2k/eDMFT bookkeeping without taking over the user's shell environment.

## Hard environment rule

`edmft_workflow` does not modify the login shell or persistent shell configuration:

- no `.bashrc` edits;
- no aliases;
- no required `env.sh`;
- no permanent `PATH`, `LD_LIBRARY_PATH`, `PYTHONPATH`, Intel/MKL/MPI exports;
- no automatic `qsub`.

Preparation helpers run in child processes only. The parent terminal remains unchanged.

## Foreground: preparation, checks, and analysis

These are lightweight login-node operations:

```bash
python /path/to/workflow.py -c config.toml init-layout
python /path/to/workflow.py -c config.toml prepare-dmft
python /path/to/workflow.py -c config.toml prepare-maxent
python /path/to/workflow.py -c config.toml prepare-dos
python /path/to/workflow.py -c config.toml prepare-band
python /path/to/workflow.py -c config.toml doctor STAGE
python /path/to/workflow.py -c config.toml check dmft
python /path/to/workflow.py -c config.toml status
python /path/to/workflow.py -c config.toml analyze all
```

Most preparation work is pure Python file management. When an official helper is needed, the workflow calls the configured Python executable plus the absolute eDMFT script path directly, for example conceptually:

```text
/home/.../envs/edmft/bin/python /home/.../eDMFT/bin/dmft_copy.py SOURCE
/home/.../envs/edmft/bin/python /home/.../eDMFT/bin/szero.py
/home/.../envs/edmft/bin/python /home/.../eDMFT/bin/saverage.py ...
```

Only minimal child-process variables such as `WIENROOT`, `WIEN_DMFT_ROOT`, and an explicitly needed `SCRATCH` are supplied. Preparation does not source Intel `compilervars.sh`, configure MKL/MPI, or rewrite PATH/LD_LIBRARY_PATH/PYTHONPATH.

## PBS: heavy numerical compute stages

The full Intel/MKL/MPI/WIEN2k/eDMFT runtime is written into standalone PBS scripts. Generate them with:

```bash
python /path/to/workflow.py -c config.toml pbs dft
python /path/to/workflow.py -c config.toml pbs dmft
python /path/to/workflow.py -c config.toml pbs maxent
python /path/to/workflow.py -c config.toml pbs dos
python /path/to/workflow.py -c config.toml pbs band
```

Then inspect and submit manually:

```bash
cat <stage>/run_STAGE.pbs
qsub <stage>/run_STAGE.pbs
```

| Stage | Native numerical command(s) | Parallel mechanism |
|---|---|---|
| DFT SCF | `run_lapw -p ...` | WIEN2k `.machines` |
| CSC DMFT | `run_dmft.py` | `mpi_prefix.dat` / eDMFT MPI |
| MaxEnt | `maxent_run.py sig.inpx` | explicit MPI launch |
| real-axis DOS | `x_lapw ... lapw0`, `x_dmft.py lapw1`, `x_dmft.py dmft1` | WIEN2k/eDMFT runtime |
| band spectral function | `x_dmft.py lapw1 --band`, `x_dmft.py dmftp` | eDMFT runtime |

There is no automatic dependency chain and no automatic submission. Each stage is inspected before `qsub`.

## Heavy-job environment

`config.toml` records installation locations such as Intel compiler/MPI/MKL, FFTW, WIEN2k, eDMFT, and Python. These values are used to render self-contained PBS scripts.

That heavy environment belongs to the PBS file, not to the user's interactive shell and not to `prepare-*`.

The generated PBS can therefore be inspected independently and contains the actual commands that will run on the compute node.

## `dmft_copy.py` rule

`dmft_copy.py SOURCE` copies files **from SOURCE into the current working directory**:

```text
cwd = PROJECT/dmft
    dmft_copy.py PROJECT/dft

cwd = PROJECT/dmft/onreal
    dmft_copy.py PROJECT/dmft

cwd = PROJECT/dmft/band
    dmft_copy.py PROJECT/dmft
```

DOS and band both start independently from the converged Matsubara `dmft/` baseline. Band does not inherit the DOS working directory.

For the initial `dft -> dmft` snapshot, the workflow supplements upstream copying with `CASE.indmf` and converged potential files such as `CASE.vsp/CASE.vns` or spin-polarized variants. Mutable numerical files are copied, not symlinked. There is no `DFT_SOURCE` symlink.

## Real-axis invariant

The converged baseline remains:

```text
PROJECT/dmft/CASE.indmfl    Matsubara flag = 1
```

`prepare-dos` and `prepare-band` work only on copies. Each preserves a `.matsubara` backup and changes the active copied file:

```text
Matsubara flag 1 -> 0
```

with its own real-frequency mesh.

## Recommended production sequence

```bash
# create project directories
python /path/to/workflow.py -c config.toml init-layout

# manual checkpoint 1
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
init_lapw

# generate / inspect / submit DFT
python /path/to/workflow.py -c PROJECT/config.toml pbs dft
cat PROJECT/dft/run_dft.pbs
qsub PROJECT/dft/run_dft.pbs

# manual checkpoint 2 after DFT convergence
cd PROJECT/dft
init_dmft.py
# inspect CASE.indmf, CASE.indmfl, CASE.indmfi

# prepare isolated DMFT snapshot
python /path/to/workflow.py -c PROJECT/config.toml prepare-dmft
python /path/to/workflow.py -c PROJECT/config.toml doctor dmft

# generate / inspect / submit CSC DMFT
python /path/to/workflow.py -c PROJECT/config.toml pbs dmft
cat PROJECT/dmft/run_dmft.pbs
qsub PROJECT/dmft/run_dmft.pbs

# check result
python /path/to/workflow.py -c PROJECT/config.toml check dmft

# MaxEnt
python /path/to/workflow.py -c PROJECT/config.toml prepare-maxent
python /path/to/workflow.py -c PROJECT/config.toml doctor maxent
python /path/to/workflow.py -c PROJECT/config.toml pbs maxent
cat PROJECT/dmft/maxent/run_maxent.pbs
qsub PROJECT/dmft/maxent/run_maxent.pbs

# DOS and band are then prepared, inspected, frozen to PBS, and qsub'ed separately
```

The central rule is: **the workflow organizes and validates the work; standalone job scripts own the heavy runtime; the user owns submission.**
