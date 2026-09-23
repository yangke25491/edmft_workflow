# Execution policy: foreground vs PBS

Only two scientific initialization steps are intentionally manual:

```text
PROJECT/dft  : init_lapw
PROJECT/dmft : init_dmft.py
```

Everything else is script-driven. The distinction below is about **where the script runs**, not whether it is automated.

## Foreground: fast bookkeeping / validation / plotting

These tasks should normally run directly in the login shell because they finish quickly and gain nothing from MPI:

```bash
edmft-workflow -c config.toml init-layout
edmft-workflow -c config.toml doctor-env
edmft-workflow -c config.toml prepare-dmft
edmft-workflow -c config.toml doctor
edmft-workflow -c config.toml check
edmft-workflow -c config.toml run plots
```

They perform directory creation, file copying, parsing, sanity checks or headless Matplotlib plotting. Starting MPI ranks for these operations would add overhead without numerical speedup.

## PBS: numerical compute stages

Production numerical stages should be submitted to PBS/Torque:

| Stage | Normal command | Parallel mechanism |
|---|---|---|
| DFT SCF | `submit dft` | WIEN2k `run_lapw -p` + `.machines` |
| charge-self-consistent DMFT | `submit dmft` | `.machines` for WIEN2k + `mpi_prefix.dat(.2)` for eDMFT |
| MaxEnt | `submit maxent` | explicit `mpirun -np N python maxent_run.py ...` |
| real-axis DOS | `submit dos` | `mpi_prefix.dat(.2)` consumed by `x_dmft.py lapw1/dmft1` |
| band spectral function | `submit band` | `mpi_prefix.dat(.2)` consumed by `x_dmft.py lapw1 --band/dmftp` |

The complete production post-processing compute chain is:

```bash
edmft-workflow -c config.toml submit post
```

which submits:

```text
maxent --afterok--> dos --afterok--> band
```

After the band job finishes, plotting is deliberately kept out of PBS:

```bash
edmft-workflow -c config.toml run plots
```

## MPI rank selection

PBS allocations are read from `PBS_NODEFILE`. In configuration:

```toml
[parallel]
mpi_launcher = "mpirun"
mpi_np_flag = "-np"
dft_ranks = "allocation"
dmft_ranks = "allocation"
maxent_ranks = "allocation"
dos_ranks = "allocation"
band_ranks = "allocation"
```

`allocation` means use the number of PBS slots. A fixed integer can cap a particular stage.

MaxEnt additionally caps the number of ranks to the number of active bath/self-energy channels, because extra MPI ranks would have no work.

WIEN2k k-point parallelism is not implemented by simply prefixing `run_lapw` with `mpirun`. The workflow generates `.machines` and uses WIEN2k's `-p` mechanism. Haule's `createW2kmachinef.py` is used to distribute the allocated PBS hosts over the available k points.

## One authoritative runtime environment

Every numerical command, whether debugged in the foreground or run under PBS, sources the same file configured by:

```toml
[environment]
setup_script = "/home/USER/.config/edmft_workflow/env.sh"
```

That file should initialize **Intel compiler + Intel MPI + MKL**, then add WIEN2k, eDMFT, FFTW and the Python/conda environment. This prevents the common situation in which an interactive shell works but a PBS node cannot find `libmkl_intel_lp64.so`, `libmkl_intel_thread.so`, or `libmkl_core.so`.

Before the first production submission, run:

```bash
edmft-workflow -c config.toml doctor-env
```

Every generated PBS script also runs this preflight before starting the expensive calculation. It checks:

- `WIENROOT` and `WIEN_DMFT_ROOT`;
- which `mpirun` is being used and its version;
- `mpi4py` and the MPI library it reports;
- dynamic linking of `ctqmc`, `dmft`, and `dmft2`;
- direct loading of the three Intel MKL shared libraries above.

A failed runtime preflight stops the PBS job before expensive work begins.

## `dmft_copy.py` rule

`dmft_copy.py SOURCE` always copies files **from SOURCE into the current working directory**. The workflow follows this literally:

```text
cwd = PROJECT/dmft
    dmft_copy.py PROJECT/dft

cwd = PROJECT/dmft/onreal
    dmft_copy.py PROJECT/dmft

cwd = PROJECT/dmft/band
    dmft_copy.py PROJECT/dmft/onreal
```

Do not reverse the argument/cwd semantics.

## Recommended production sequence

```bash
# once per material
edmft-workflow -c config.toml init-layout

# manual checkpoint 1
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
init_lapw

# heavy DFT
edmft-workflow -c config.toml doctor-env
edmft-workflow -c config.toml submit dft

# after DFT completes
edmft-workflow -c config.toml prepare-dmft

# manual checkpoint 2
cd PROJECT/dmft
init_dmft.py
# inspect/edit indmf*, indmfl, indmfi and params.dat

# heavy CSC DFT+DMFT
edmft-workflow -c config.toml submit dmft

# after DMFT completes
edmft-workflow -c config.toml check
edmft-workflow -c config.toml submit post

# after post chain completes
edmft-workflow -c config.toml run plots
```
