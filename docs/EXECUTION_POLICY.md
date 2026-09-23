# Execution policy: foreground vs PBS

Only two scientific initialization steps are intentionally manual, and both are performed in the complete DFT directory:

```text
PROJECT/dft : init_lapw      # before DFT
PROJECT/dft : init_dmft.py   # after DFT convergence
```

Everything else is script-driven. The distinction below is about **where the script runs**, not whether it is automated.

## Foreground: fast bookkeeping / validation / plotting

These tasks normally run directly in the login shell:

```bash
python /path/to/workflow.py -c config.toml init-layout
python /path/to/workflow.py -c config.toml doctor-env
python /path/to/workflow.py -c config.toml prepare-dmft
python /path/to/workflow.py -c config.toml doctor
python /path/to/workflow.py -c config.toml check
python /path/to/workflow.py -c config.toml run plots
```

`prepare-dmft` is bookkeeping rather than a heavy numerical stage: it copies the initialized DFT snapshot, supplements files omitted by `dmft_copy.py`, writes `params.dat`, and runs the short `szero.py` initialization.

## PBS: numerical compute stages

| Stage | Normal command | Parallel mechanism |
|---|---|---|
| DFT SCF | `submit dft` | WIEN2k `run_lapw -p` + `.machines` |
| charge-self-consistent DMFT | `submit dmft` | `mpi_prefix.dat(.2)` for eDMFT; optional WIEN2k `.machines` |
| MaxEnt | `submit maxent` | explicit MPI launch |
| real-axis DOS | `submit dos` | `mpi_prefix.dat(.2)` consumed by `x_dmft.py` |
| band spectral function | `submit band` | `mpi_prefix.dat(.2)` consumed by `x_dmft.py` |

The complete numerical post-processing chain is:

```text
maxent --afterok--> dos --afterok--> band
```

Plotting stays in the foreground after those jobs complete.

## MPI rank selection

`"allocation"` means use the PBS allocation read from `PBS_NODEFILE`:

```toml
[parallel]
dft_ranks = "allocation"
dmft_ranks = "allocation"
maxent_ranks = "allocation"
dos_ranks = "allocation"
band_ranks = "allocation"
```

WIEN2k DFT parallelism is controlled by `.machines` plus `run_lapw -p`, not by simply prefixing `run_lapw` with `mpirun`. On the validated single-node cluster setup, `wien_machines_mode = "single_node_compact"` reproduces:

```text
1:<host>:<NP>
granularity:1
extrafine:1
```

## Config-only runtime environment

No external `env.sh` is required. The project config records the Intel compiler/MPI/MKL, FFTW, WIEN2k, eDMFT and Python installation locations. The workflow constructs the corresponding runtime environment inside foreground subprocesses and generated PBS jobs.

Before expensive submissions:

```bash
python /path/to/workflow.py -c config.toml doctor-env
```

The generated PBS script runs the same preflight before starting numerical work.

## `dmft_copy.py` rule

`dmft_copy.py SOURCE` copies files **from SOURCE into the current working directory**:

```text
cwd = PROJECT/dmft
    dmft_copy.py PROJECT/dft

cwd = PROJECT/dmft/onreal
    dmft_copy.py PROJECT/dmft

cwd = PROJECT/dmft/band
    dmft_copy.py PROJECT/dmft/onreal
```

For the initial `dft -> dmft` snapshot, the workflow additionally copies `case.indmf`, `case.vsp/case.vns`, and spin-polarized potential variants when present. These mutable numerical files are copied, not symlinked. `DFT_SOURCE -> ../dft` is provenance only.

## Recommended production sequence

```bash
# once per material
python /path/to/workflow.py -c config.toml init-layout

# manual checkpoint 1
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
init_lapw

# heavy DFT
python /path/to/workflow.py -c PROJECT/config.toml doctor-env
python /path/to/workflow.py -c PROJECT/config.toml submit dft

# manual checkpoint 2, still in the converged DFT directory
cd PROJECT/dft
init_dmft.py
# inspect/edit case.indmf, case.indmfl, case.indmfi

# automated isolated snapshot + params.dat + initial sig.inp
python /path/to/workflow.py -c PROJECT/config.toml prepare-dmft

# heavy CSC DFT+DMFT
python /path/to/workflow.py -c PROJECT/config.toml submit dmft

# post-processing
python /path/to/workflow.py -c PROJECT/config.toml check
python /path/to/workflow.py -c PROJECT/config.toml submit post
python /path/to/workflow.py -c PROJECT/config.toml run plots
```
