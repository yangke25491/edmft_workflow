# Execution policy: zero-interference preparation and standalone PBS

Only two scientific initialization steps are intentionally manual:

```text
PROJECT/dft : init_lapw      # before DFT
PROJECT/dft : init_dmft.py   # after DFT convergence
```

The workflow reduces WIEN2k/eDMFT bookkeeping without taking over the user's login shell.

## Two configuration layers

```text
config.toml
    physics + requested resources + frequently tuned commands

site.toml
    machine software paths + runtime setup + MPI + scheduler semantics
```

`site.toml` is ignored by git. A generated PBS script freezes both configuration layers into a standalone script; compute nodes do not need either TOML file.

## Hard environment rule

`edmft_workflow` does not modify persistent shell state:

- no `.bashrc` edits;
- no aliases;
- no required sourced `env.sh`;
- no permanent `PATH`, `LD_LIBRARY_PATH`, `PYTHONPATH`, compiler/MKL/MPI exports;
- no automatic `qsub`.

Preparation helpers and foreground numerical work run in child processes only.

## Lightweight foreground operations

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

Official lightweight eDMFT helpers are invoked through the Python/eDMFT locations supplied by `site.toml`. Preparation does not need to source the full numerical runtime.

## Heavy PBS stages

PBS is reserved for:

```text
DFT
DMFT
MaxEnt
```

Generate but do not submit:

```bash
python /path/to/workflow.py -c config.toml pbs dft
python /path/to/workflow.py -c config.toml pbs dmft
python /path/to/workflow.py -c config.toml pbs maxent
```

Then inspect and submit manually:

```bash
cat <stage>/run_STAGE.pbs
qsub <stage>/run_STAGE.pbs
```

| Stage | Native numerical command(s) | Parallel mechanism |
|---|---|---|
| DFT SCF | `run_lapw -p ...` | WIEN2k `.machines` |
| CSC DMFT | `run_dmft.py` | native MPI via `mpi_prefix.dat` |
| MaxEnt | `maxent_run.py sig.inpx` | site-defined mpi4py-compatible MPI |

## Foreground numerical postprocessing

DOS and band are not PBS stages:

```bash
python /path/to/workflow.py -c config.toml prepare-dos
python /path/to/workflow.py -c config.toml doctor dos
python /path/to/workflow.py -c config.toml run dos

python /path/to/workflow.py -c config.toml prepare-band
python /path/to/workflow.py -c config.toml doctor band
python /path/to/workflow.py -c config.toml run band
```

They run in disposable child shells using the native MPI/runtime from `site.toml`. The rank counts remain calculation choices in `[foreground]` inside `config.toml`.

## Scheduler behavior is site data

The workflow does not branch on scheduler product names. `site.toml` defines:

```text
job-id environment variable
nodefile environment variable
slot-count source (nodefile/env/command/auto)
optional slot-count command
resource request template
submit command
```

Thus a site whose nodefile contains one line per CPU slot and a site whose nodefile contains only hosts can use the same Python code with different site profiles.

## Runtime behavior is site data

`site.toml` also defines:

```text
WIENROOT / eDMFT / Python locations
runtime setup commands
PATH / LD_LIBRARY_PATH additions
runtime environment variables
native MPI launcher
MaxEnt MPI launcher
optional shared-library checks
```

No particular MPI implementation or MKL library is required by workflow logic.

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

DOS and band both start independently from the converged Matsubara `dmft/` baseline.

## Real-axis invariant

The converged baseline remains:

```text
PROJECT/dmft/CASE.indmfl    Matsubara flag = 1
```

`prepare-dos` and `prepare-band` preserve a `.matsubara` backup and modify only their copied active files:

```text
Matsubara flag 1 -> 0
```

## Production sequence

```bash
python /path/to/workflow.py -c config.toml init-layout

cd PROJECT/dft
init_lapw

python /path/to/workflow.py -c PROJECT/config.toml pbs dft
cat PROJECT/dft/run_dft.pbs
qsub PROJECT/dft/run_dft.pbs

cd PROJECT/dft
init_dmft.py

python /path/to/workflow.py -c PROJECT/config.toml prepare-dmft
python /path/to/workflow.py -c PROJECT/config.toml doctor dmft
python /path/to/workflow.py -c PROJECT/config.toml pbs dmft
cat PROJECT/dmft/run_dmft.pbs
qsub PROJECT/dmft/run_dmft.pbs

python /path/to/workflow.py -c PROJECT/config.toml check dmft

python /path/to/workflow.py -c PROJECT/config.toml prepare-maxent
python /path/to/workflow.py -c PROJECT/config.toml doctor maxent
python /path/to/workflow.py -c PROJECT/config.toml pbs maxent
cat PROJECT/dmft/maxent/run_maxent.pbs
qsub PROJECT/dmft/maxent/run_maxent.pbs

python /path/to/workflow.py -c PROJECT/config.toml prepare-dos
python /path/to/workflow.py -c PROJECT/config.toml run dos

python /path/to/workflow.py -c PROJECT/config.toml prepare-band
python /path/to/workflow.py -c PROJECT/config.toml run band
```

The central rule is: **config.toml describes this calculation; site.toml describes this machine; generated job scripts own the frozen runtime; the user owns submission.**
